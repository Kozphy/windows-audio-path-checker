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
        "bluetooth": {
            "association_service": {
                "name": "DeviceAssociationService",
                "status": "Running",
            },
            "bluetooth_service": {"name": "bthserv", "status": "Running"},
            "paired_headsets": [],
            "default_endpoint_present": True,
            "default_endpoint_name": "Headphones (USB Audio Device)",
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
                "output_device": "Headphones (USB Audio Device)",
            }
        ]

        findings = analyze_snapshot(snapshot)
        by_code = {finding["code"]: finding for finding in findings}

        self.assertEqual(
            by_code["browser-session-silent"]["severity"], "critical"
        )
        self.assertIn("msedge.exe", by_code["browser-session-silent"]["detail"])

    def test_edge_at_zero_volume_is_critical(self):
        """Regression: Windows test works, Edge session volume is 0%."""
        snapshot = base_snapshot()
        snapshot["core_audio"]["sessions"] = [
            {
                "process": "msedge.exe",
                "volume": 0.0,
                "muted": False,
                "is_browser": True,
                "output_device": "Headphones (EDIFIER W800BT Pro)",
            }
        ]
        snapshot["core_audio"]["default_endpoint"] = {
            "name": "Headphones (EDIFIER W800BT Pro)"
        }
        snapshot["portaudio"]["default_output_name"] = (
            "Headphones (EDIFIER W800BT Pro)"
        )
        snapshot["portaudio"]["output_devices"] = [
            {
                "index": 4,
                "name": "Headphones (EDIFIER W800BT Pro)",
                "host_api": "Windows WASAPI",
                "is_default": True,
            }
        ]

        findings = analyze_snapshot(snapshot)
        by_code = {finding["code"]: finding for finding in findings}

        self.assertEqual(
            by_code["browser-session-silent"]["severity"], "critical"
        )
        self.assertIn("0%", by_code["browser-session-silent"]["detail"])
        self.assertIn("msedge.exe", by_code["browser-session-silent"]["detail"])
        self.assertNotIn("browser-output-mismatch", by_code)

    def test_browser_on_wrong_output_is_a_warning(self):
        snapshot = base_snapshot()
        snapshot["core_audio"]["sessions"] = [
            {
                "process": "msedge.exe",
                "volume": 0.8,
                "muted": False,
                "is_browser": True,
                "state": "1",
                "output_device": "XZ240Q (NVIDIA High Definition Audio)",
            }
        ]

        findings = analyze_snapshot(snapshot)
        by_code = {finding["code"]: finding for finding in findings}

        self.assertEqual(
            by_code["browser-output-mismatch"]["severity"], "warning"
        )
        self.assertIn("XZ240Q", by_code["browser-output-mismatch"]["detail"])
        self.assertIn(
            "Volume mixer", by_code["browser-output-mismatch"]["action"]
        )

    def test_stale_browser_on_secondary_device_is_not_a_warning(self):
        snapshot = base_snapshot()
        snapshot["core_audio"]["sessions"] = [
            {
                "process": "msedge.exe",
                "volume": 0.5,
                "muted": False,
                "is_browser": True,
                "state": "0",
                "output_device": "Headphones (USB Audio Device)",
            },
            {
                "process": "msedge.exe",
                "volume": 1.0,
                "muted": False,
                "is_browser": True,
                "state": "0",
                "output_device": "XZ240Q (NVIDIA High Definition Audio)",
            },
        ]

        findings = analyze_snapshot(snapshot)
        by_code = {finding["code"]: finding for finding in findings}

        self.assertNotIn("browser-output-mismatch", by_code)
        self.assertEqual(
            by_code["browser-session-visible"]["severity"], "ok"
        )

    def test_low_master_volume_is_a_warning(self):
        snapshot = base_snapshot()
        snapshot["core_audio"]["master_volume"] = 0.15

        findings = analyze_snapshot(snapshot)
        by_code = {finding["code"]: finding for finding in findings}

        self.assertEqual(by_code["master-volume-low"]["severity"], "warning")
        self.assertIn("15%", by_code["master-volume-low"]["detail"])
        self.assertNotIn("master-volume-ok", by_code)

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

    def test_bluetooth_endpoint_not_present_is_a_warning(self):
        snapshot = base_snapshot()
        snapshot["core_audio"]["default_endpoint"] = {
            "name": "Headphones (EDIFIER W800BT Pro)"
        }
        snapshot["portaudio"]["default_output_name"] = (
            "Headphones (EDIFIER W800BT Pro)"
        )
        snapshot["portaudio"]["output_devices"] = [
            {
                "index": 4,
                "name": "Headphones (EDIFIER W800BT Pro)",
                "host_api": "Windows WASAPI",
                "is_default": True,
            }
        ]
        snapshot["bluetooth"] = {
            "association_service": {
                "name": "DeviceAssociationService",
                "status": "Running",
            },
            "paired_headsets": [
                {
                    "name": "EDIFIER W800BT Pro",
                    "address": "c8247887e57c",
                    "last_connected": None,
                    "is_present": True,
                }
            ],
            "default_endpoint_present": False,
            "default_endpoint_name": "Headphones (EDIFIER W800BT Pro)",
        }

        findings = analyze_snapshot(snapshot)
        by_code = {finding["code"]: finding for finding in findings}

        self.assertEqual(
            by_code["bluetooth-audio-ui-desync"]["severity"], "warning"
        )
        self.assertIn(
            "Repair Bluetooth pairing",
            by_code["bluetooth-audio-ui-desync"]["action"],
        )

    def test_bluetooth_association_service_stopped_is_a_warning(self):
        snapshot = base_snapshot()
        snapshot["bluetooth"]["association_service"] = {
            "name": "DeviceAssociationService",
            "status": "Stopped",
        }

        findings = analyze_snapshot(snapshot)
        by_code = {finding["code"]: finding for finding in findings}

        self.assertEqual(
            by_code["bluetooth-association-service"]["severity"], "warning"
        )


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
