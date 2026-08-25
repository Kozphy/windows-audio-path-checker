import unittest

from audio_path_checker.bluetooth_inference import infer_bluetooth_state
from audio_path_checker.inference import enrich_snapshot


class BluetoothInferenceTests(unittest.TestCase):
    def test_healthy_adapter_means_capability_present(self):
        snapshot = {
            "bluetooth": {
                "adapters": [
                    {
                        "name": "MediaTek Bluetooth Adapter",
                        "status": "OK",
                        "is_present": True,
                        "problem_code": 0,
                        "config_manager_error": None,
                    }
                ],
                "paired_headsets": [],
                "bluetooth_service": {"status": "Running"},
            }
        }
        result = infer_bluetooth_state(snapshot)
        self.assertEqual(result["capability"], "present")
        self.assertEqual(result["top_hypothesis"]["code"], "bluetooth-capability-present")
        self.assertGreaterEqual(result["top_hypothesis"]["probability"], 0.99)

    def test_paired_history_without_adapter_suggests_enumeration_problem(self):
        snapshot = {
            "bluetooth": {
                "adapters": [],
                "paired_headsets": [
                    {
                        "name": "EDIFIER W800BT Pro",
                        "address": "c8247887e57c",
                        "is_present": False,
                    }
                ],
            }
        }
        result = infer_bluetooth_state(snapshot)
        self.assertEqual(result["capability"], "historical-likely")
        self.assertEqual(result["top_hypothesis"]["code"], "bluetooth-capability-historical")

    def test_connected_device_without_audio_endpoint_is_ranked(self):
        snapshot = {
            "core_audio": {"default_endpoint": {"name": "Speakers"}},
            "bluetooth": {
                "adapters": [
                    {
                        "name": "Intel Wireless Bluetooth",
                        "status": "OK",
                        "is_present": True,
                        "problem_code": 0,
                    }
                ],
                "paired_headsets": [
                    {
                        "name": "Sony WH-1000XM5",
                        "is_present": True,
                    }
                ],
                "default_endpoint_present": False,
                "bluetooth_service": {"status": "Running"},
                "avctp_service": {"status": "Running"},
                "audio_gateway_service": {"status": "Running"},
            },
        }
        result = infer_bluetooth_state(snapshot)
        codes = [item["code"] for item in result["hypotheses"]]
        self.assertIn("bluetooth-audio-endpoint-missing", codes)
        self.assertEqual(result["connection"], "present")

    def test_paired_but_not_present_is_distinguished_from_no_bluetooth(self):
        snapshot = {
            "bluetooth": {
                "adapters": [
                    {
                        "name": "Intel Wireless Bluetooth",
                        "status": "OK",
                        "is_present": True,
                        "problem_code": 0,
                    }
                ],
                "paired_headsets": [
                    {"name": "AirPods Pro", "is_present": False}
                ],
            }
        }
        result = infer_bluetooth_state(snapshot)
        self.assertEqual(result["connection"], "paired-not-present")
        codes = [item["code"] for item in result["hypotheses"]]
        self.assertIn("headset-not-currently-connected", codes)

    def test_enrich_snapshot_exposes_bluetooth_path(self):
        snapshot = {"findings": [], "bluetooth": {"adapters": [], "paired_headsets": []}}
        enriched = enrich_snapshot(snapshot)
        self.assertEqual(enriched["inference"]["schema_version"], 2)
        self.assertIn("bluetooth_path", enriched["inference"])
        self.assertEqual(enriched["inference"]["bluetooth_path"]["capability"], "unknown")


if __name__ == "__main__":
    unittest.main()
