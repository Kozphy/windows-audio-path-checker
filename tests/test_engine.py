import unittest

from audio_path_checker.engine import build_diagnosis, build_evidence, rank_hypotheses


def snapshot_with_muted_browser():
    return {
        "system": {"is_windows": True},
        "services": [
            {"name": "Audiosrv", "friendly_name": "Windows Audio", "status": "running"},
            {
                "name": "AudioEndpointBuilder",
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
            "master_volume": 0.6,
            "master_muted": False,
            "sessions": [
                {
                    "process": "msedge.exe",
                    "volume": 0.0,
                    "muted": False,
                    "is_browser": True,
                    "output_device": "Headphones (USB Audio Device)",
                }
            ],
        },
        "errors": [],
        "findings": [
            {
                "severity": "critical",
                "code": "browser-session-silent",
                "title": "A browser audio session is muted or at zero",
                "detail": "msedge.exe (0%)",
                "action": "Unmute the browser session.",
            }
        ],
    }


class EvidenceEngineTests(unittest.TestCase):
    def test_builds_normalized_evidence(self):
        evidence = build_evidence(snapshot_with_muted_browser())
        by_id = {item.id: item for item in evidence}

        self.assertEqual(by_id["service:Audiosrv"].status, "pass")
        self.assertEqual(by_id["core-audio:master-volume"].status, "pass")
        self.assertEqual(by_id["session:msedge.exe:0"].status, "fail")

    def test_muted_browser_is_top_hypothesis(self):
        hypotheses = rank_hypotheses(snapshot_with_muted_browser())

        self.assertEqual(hypotheses[0].code, "browser-session-silent")
        self.assertGreaterEqual(hypotheses[0].confidence, 0.9)
        self.assertIn("session:msedge.exe:0", hypotheses[0].evidence_ids)

    def test_build_diagnosis_is_report_ready(self):
        diagnosis = build_diagnosis(snapshot_with_muted_browser())

        self.assertEqual(diagnosis["engine_version"], 1)
        self.assertEqual(
            diagnosis["primary_hypothesis"]["code"], "browser-session-silent"
        )
        self.assertTrue(diagnosis["summary"]["scan_complete"])
        self.assertGreater(diagnosis["summary"]["evidence_count"], 0)

    def test_collector_errors_mark_scan_incomplete(self):
        snapshot = snapshot_with_muted_browser()
        snapshot["errors"] = [
            {"source": "Windows Core Audio", "message": "COM unavailable"}
        ]

        diagnosis = build_diagnosis(snapshot)

        self.assertFalse(diagnosis["summary"]["scan_complete"])


if __name__ == "__main__":
    unittest.main()
