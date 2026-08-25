import unittest

from audio_path_checker.device_graph import build_device_graph
from audio_path_checker.inference import enrich_snapshot


class DeviceGraphTests(unittest.TestCase):
    def test_builds_bluetooth_to_endpoint_to_browser_path(self):
        snapshot = {
            "bluetooth": {
                "adapters": [
                    {
                        "name": "MediaTek Bluetooth Adapter",
                        "status": "OK",
                        "instance_id": "USB\\VID_0E8D",
                        "problem_code": 0,
                        "is_present": True,
                    }
                ],
                "paired_headsets": [
                    {
                        "name": "EDIFIER W800BT Pro",
                        "address": "c8247887e57c",
                        "status": "OK",
                        "is_present": True,
                    }
                ],
                "default_endpoint_present": True,
            },
            "core_audio": {
                "default_endpoint": {
                    "name": "Headphones (EDIFIER W800BT Pro)",
                    "id": "endpoint-1",
                },
                "sessions": [
                    {
                        "process": "chrome.exe",
                        "pid": 1234,
                        "instance_id": "session-1",
                        "volume": 0.8,
                        "muted": False,
                        "state": "Active",
                        "is_browser": True,
                        "output_device": "Headphones (EDIFIER W800BT Pro)",
                    }
                ],
            },
            "portaudio": {
                "output_devices": [
                    {
                        "index": 3,
                        "name": "Headphones (EDIFIER W800BT Pro)",
                        "host_api": "Windows WASAPI",
                        "is_default": True,
                    }
                ]
            },
        }

        graph = build_device_graph(snapshot)
        kinds = {node["kind"] for node in graph["nodes"]}
        relations = {edge["relation"] for edge in graph["edges"]}

        self.assertIn("bluetooth-adapter", kinds)
        self.assertIn("bluetooth-audio-device", kinds)
        self.assertIn("audio-endpoint", kinds)
        self.assertIn("audio-session", kinds)
        self.assertIn("radio-link", relations)
        self.assertIn("exposes-audio-endpoint", relations)
        self.assertIn("routes-to", relations)
        self.assertEqual(graph["summary"]["breakpoint_count"], 0)
        self.assertTrue(graph["summary"]["browser_session_observed"])

    def test_flags_unhealthy_adapter(self):
        graph = build_device_graph(
            {
                "bluetooth": {
                    "adapters": [
                        {
                            "name": "Bluetooth Adapter",
                            "status": "Error",
                            "instance_id": "USB\\BAD",
                            "problem_code": 22,
                        }
                    ]
                }
            }
        )
        codes = {item["code"] for item in graph["breakpoints"]}
        self.assertIn("bluetooth-adapter-unhealthy", codes)

    def test_flags_paired_but_disconnected_device(self):
        graph = build_device_graph(
            {
                "bluetooth": {
                    "adapters": [
                        {
                            "name": "Bluetooth Adapter",
                            "status": "OK",
                            "instance_id": "USB\\GOOD",
                            "problem_code": 0,
                        }
                    ],
                    "paired_headsets": [
                        {
                            "name": "Headphones",
                            "address": "001122334455",
                            "is_present": False,
                        }
                    ],
                }
            }
        )
        codes = {item["code"] for item in graph["breakpoints"]}
        self.assertIn("bluetooth-device-not-present", codes)

    def test_flags_silent_browser_session(self):
        graph = build_device_graph(
            {
                "core_audio": {
                    "default_endpoint": {"name": "Speakers", "id": "ep"},
                    "sessions": [
                        {
                            "process": "chrome.exe",
                            "instance_id": "chrome-session",
                            "volume": 0.0,
                            "muted": False,
                            "is_browser": True,
                        }
                    ],
                }
            }
        )
        codes = {item["code"] for item in graph["breakpoints"]}
        self.assertIn("session-silent", codes)

    def test_enriched_snapshot_contains_graph_schema_v3(self):
        enriched = enrich_snapshot({"findings": []})
        self.assertEqual(enriched["inference"]["schema_version"], 3)
        self.assertIn("device_graph", enriched["inference"])
        self.assertIn("summary", enriched["inference"]["device_graph"])


if __name__ == "__main__":
    unittest.main()
