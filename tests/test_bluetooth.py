import unittest

from audio_path_checker.bluetooth import (
    disabled_bluetooth_adapters,
    match_headset_for_endpoint,
    preferred_bluetooth_repair_target,
)


class BluetoothMatchTests(unittest.TestCase):
    def test_match_edifier_endpoint(self):
        bluetooth = {
            "paired_headsets": [
                {
                    "name": "POLYWELL TM-086",
                    "address": "90fc4895be62",
                },
                {
                    "name": "EDIFIER W800BT Pro",
                    "address": "c8247887e57c",
                },
            ]
        }
        matched = match_headset_for_endpoint(
            bluetooth, "Headphones (EDIFIER W800BT Pro)"
        )
        self.assertIsNotNone(matched)
        assert matched is not None
        self.assertEqual(matched["address"], "c8247887e57c")

    def test_preferred_repair_target_uses_default_endpoint(self):
        snapshot = {
            "core_audio": {
                "default_endpoint": {
                    "name": "Headphones (EDIFIER W800BT Pro)"
                }
            },
            "bluetooth": {
                "paired_headsets": [
                    {
                        "name": "EDIFIER WH700NB",
                        "address": "cc14bc0bde24",
                    },
                    {
                        "name": "EDIFIER W800BT Pro",
                        "address": "c8247887e57c",
                    },
                ]
            },
        }
        target = preferred_bluetooth_repair_target(snapshot)
        self.assertIsNotNone(target)
        assert target is not None
        self.assertEqual(target["address"], "c8247887e57c")

    def test_disabled_adapter_detection(self):
        snapshot = {
            "bluetooth": {
                "adapters": [
                    {
                        "name": "MediaTek Bluetooth Adapter",
                        "status": "OK",
                        "instance_id": "USB\\OK",
                        "problem_code": 0,
                    },
                    {
                        "name": "MediaTek Bluetooth Adapter",
                        "status": "Error",
                        "instance_id": "USB\\BAD",
                        "problem_code": 22,
                        "config_manager_error": "CM_PROB_DISABLED",
                    },
                ]
            }
        }
        bad = disabled_bluetooth_adapters(snapshot)
        self.assertEqual(len(bad), 1)
        self.assertEqual(bad[0]["instance_id"], "USB\\BAD")


if __name__ == "__main__":
    unittest.main()
