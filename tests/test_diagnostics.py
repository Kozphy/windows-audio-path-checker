import json
import unittest

from audio_path_checker.diagnostics import (
    analyze_snapshot,
    likely_same_device,
)


def base_snapshot():
    return {
        "system": {"is_windows": True},
        "services": [
            {"friendly_name": "Windows Audio", "status": "running"},
            {
                "friendly_name": "Windows Audio Endpoint Builder",
                "status": "running",
            },
        ],
        "portaudio": {
            "default_output_name": "Headphones (USB Audio Device)",
            "output_devices": [
                {
                    "index": 1,
                    "name": "Headphones (USB Audio Device)",
                    "host_api": "Windows WASAPI",
                    "is_default": True,
                }
            ],
        },
        "core_audio": {
            "default_endpoint": {"name": "Headphones (USB Audio Device)"},
            "master_volume": 0.4,
            "master_muted": False,
            "sessions": [],
        },
        "errors": [],
    }


class AnalyzeSnapshotTests(unittest.TestCase):
    def test_muted_browser_is_a_fix_item(self):
        snapshot = base_snapshot()
        snapshot["core_audio"]["sessions"] = [
            {
                "process": "msedge.exe",
                "volume": 0.7,
                "muted": True,
                "is_browser": True,
            }
        ]

        findings = analyze_snapshot(snapshot)
        by_code = {finding["code"]: finding for finding in findings}

        self.assertEqual(
            by_code["browser-session-silent"]["severity"], "critical"
        )
        self.assertIn("msedge.exe", by_code["browser-session-silent"]["detail"])

    def test_missing_browser_gives_guidance(self):
        findings = analyze_snapshot(base_snapshot())
        by_code = {finding["code"]: finding for finding in findings}

        self.assertEqual(
            by_code["browser-session-missing"]["severity"], "warning"
        )
        self.assertIn("YouTube", by_code["browser-session-missing"]["action"])

    def test_stopped_service_is_critical(self):
        snapshot = base_snapshot()
        snapshot["services"][0]["status"] = "stopped"

        findings = analyze_snapshot(snapshot)
        by_code = {finding["code"]: finding for finding in findings}

        self.assertEqual(
            by_code["audio-service-stopped"]["severity"], "critical"
        )

    def test_report_is_json_serializable(self):
        snapshot = base_snapshot()
        snapshot["findings"] = analyze_snapshot(snapshot)

        encoded = json.dumps(snapshot)

        self.assertIn("app-output-available", encoded)


class DeviceNameTests(unittest.TestCase):
    def test_equivalent_device_names(self):
        self.assertTrue(
            likely_same_device(
                "Headphones (Realtek(R) Audio)",
                "Headphones (2- Realtek(R) Audio)",
            )
        )

    def test_distinct_device_names(self):
        self.assertFalse(
            likely_same_device(
                "Headphones (Realtek USB)",
                "DELL U2723QE (Intel Display)",
            )
        )


if __name__ == "__main__":
    unittest.main()

