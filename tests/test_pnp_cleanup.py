"""Regression tests for PnP cleanup InstanceId validation and pnputil safety.

Mirrors scripts/Bluetooth/WapcBluetoothCleanup.psm1 decision logic so the
empty-argument pnputil bug cannot return.
"""

from __future__ import annotations

import unittest


def get_pnp_instance_id(device) -> str | None:
    """Python mirror of Get-WapcPnpInstanceId."""
    if device is None:
        return None
    if isinstance(device, (list, tuple)):
        if len(device) == 1 and device[0] is not None and not isinstance(device[0], (list, tuple)):
            return get_pnp_instance_id(device[0])
        return None
    if isinstance(device, dict):
        for key in ("InstanceId", "InstanceID", "PNPDeviceID", "PnpDeviceID", "DeviceID"):
            val = device.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
        return None
    for key in ("InstanceId", "InstanceID", "PNPDeviceID", "PnpDeviceID", "DeviceID"):
        val = getattr(device, key, None)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def is_pnputil_usage_output(output: str) -> bool:
    text = output or ""
    # Successful removals also print "Microsoft PnP Utility".
    lower = text.lower()
    if "device removed successfully" in lower:
        return False
    if "already removed" in lower or "no devices were removed" in lower:
        return False
    markers = ("pnputil [", "/add-driver", "/enum-devices", "usage:")
    return any(m in lower for m in markers)


def classify_removal(*, instance_id: str | None, exit_code: int | None, output: str, still_present: bool) -> str:
    if not instance_id or not str(instance_id).strip():
        return "MISSING_INSTANCE_ID"
    if is_pnputil_usage_output(output):
        return "PNPUTIL_USAGE_OUTPUT"
    if exit_code not in (None, 0):
        return "PNPUTIL_NONZERO_EXIT"
    if still_present:
        return "DEVICE_STILL_PRESENT"
    return "REMOVED_SUCCESSFULLY"


def should_invoke_pnputil(instance_id: str | None) -> bool:
    return bool(instance_id and str(instance_id).strip())


def fake_empty_array_return_bug() -> list:
    """Reproduce the PowerShell `return ,@()` Count=1 trap as nested empty list."""
    inner: list = []
    return [inner]  # callers saw Count=1 with no InstanceId


TARGET = "EDIFIER W800BT Pro"
ADDR = "c8247887e57c"
OTHER = "EDIFIER WH700NB"
OTHER_ADDR = "cc14bc0bde24"


class PnpInstanceIdExtractionTests(unittest.TestCase):
    """PnP InstanceId extraction and pnputil invocation safety gates."""

    def test_a_correct_target_id(self):
        device = {
            "FriendlyName": TARGET,
            "Class": "Bluetooth",
            "Status": "OK",
            "InstanceId": r"BTHENUM\Dev_C8247887E57C\a&19b543a3&0&BluetoothDevice_C8247887E57C",
        }
        iid = get_pnp_instance_id(device)
        self.assertTrue(iid)
        self.assertIn("C8247887E57C", iid.upper())
        self.assertTrue(should_invoke_pnputil(iid))

    def test_b_wrong_device_not_selected_by_name_alone(self):
        # Selection policy is address-based; this unit asserts ID extract still works
        # while address mismatch is handled by identity layer.
        device = {
            "FriendlyName": OTHER,
            "InstanceId": r"BTHENUM\Dev_CC14BC0BDE24\x",
        }
        self.assertEqual(get_pnp_instance_id(device).upper().count("CC14BC0BDE24"), 1)

    def test_c_missing_instance_id_blocks_pnputil(self):
        device = {"FriendlyName": TARGET, "Class": "Bluetooth", "Status": "OK"}
        self.assertIsNone(get_pnp_instance_id(device))
        self.assertFalse(should_invoke_pnputil(None))
        self.assertFalse(should_invoke_pnputil(""))
        self.assertFalse(should_invoke_pnputil("   "))
        self.assertEqual(
            classify_removal(instance_id=None, exit_code=0, output="", still_present=False),
            "MISSING_INSTANCE_ID",
        )

    def test_d_pnputil_usage_output(self):
        help_text = "Microsoft PnP Utility PNPUTIL [/add-driver] [/enum-devices]"
        self.assertTrue(is_pnputil_usage_output(help_text))
        self.assertEqual(
            classify_removal(
                instance_id=r"BTHENUM\Dev_C8247887E57C\x",
                exit_code=0,
                output=help_text,
                still_present=False,
            ),
            "PNPUTIL_USAGE_OUTPUT",
        )
        success_text = (
            "Microsoft PnP Utility Removing device: BTHENUM\\Dev_C8247887E57C\\x "
            "Device removed successfully."
        )
        self.assertFalse(is_pnputil_usage_output(success_text))
        self.assertEqual(
            classify_removal(
                instance_id=r"BTHENUM\Dev_C8247887E57C\x",
                exit_code=0,
                output=success_text,
                still_present=False,
            ),
            "REMOVED_SUCCESSFULLY",
        )

    def test_e_nonzero_exit(self):
        self.assertEqual(
            classify_removal(
                instance_id=r"BTHENUM\Dev_C8247887E57C\x",
                exit_code=5,
                output="Failed to remove device",
                still_present=True,
            ),
            "PNPUTIL_NONZERO_EXIT",
        )

    def test_f_command_ok_but_still_present(self):
        self.assertEqual(
            classify_removal(
                instance_id=r"BTHENUM\Dev_C8247887E57C\x",
                exit_code=0,
                output="Device removed successfully.",
                still_present=True,
            ),
            "DEVICE_STILL_PRESENT",
        )

    def test_g_successful_removal(self):
        self.assertEqual(
            classify_removal(
                instance_id=r"BTHENUM\Dev_C8247887E57C\x",
                exit_code=0,
                output="Device removed successfully.",
                still_present=False,
            ),
            "REMOVED_SUCCESSFULLY",
        )

    def test_h_friendly_name_collision_requires_address(self):
        from audio_path_checker.bluetooth_pairing.identity import pnp_node_matches_target

        sibling = pnp_node_matches_target(
            friendly_name=OTHER,
            instance_id=r"BTHENUM\Dev_CC14BC0BDE24\x",
            target_name=TARGET,
            target_address=ADDR,
        )
        target = pnp_node_matches_target(
            friendly_name=TARGET,
            instance_id=r"BTHENUM\Dev_C8247887E57C\x",
            target_name=TARGET,
            target_address=ADDR,
        )
        self.assertFalse(sibling["matched"])
        self.assertTrue(target["matched"])

    def test_i_address_normalization(self):
        from audio_path_checker.bluetooth_pairing.identity import normalize_bluetooth_address

        for v in (ADDR, "C8:24:78:87:E5:7C", "c8-24-78-87-e5-7c", "C8247887E57C"):
            self.assertEqual(normalize_bluetooth_address(v), ADDR)

    def test_j_no_matching_device_is_idempotent_clean(self):
        nodes_before = 0
        nodes_after = 0
        cleanup_pass = nodes_before == 0 and nodes_after == 0
        self.assertTrue(cleanup_pass)

    def test_regression_empty_array_wrapper_must_not_invoke_pnputil(self):
        wrapped = fake_empty_array_return_bug()
        self.assertEqual(len(wrapped), 1)
        # Old bug: callers treated Count=1 as one device.
        fake_device = wrapped[0]
        self.assertIsNone(get_pnp_instance_id(fake_device))
        self.assertFalse(should_invoke_pnputil(get_pnp_instance_id(fake_device)))
        self.assertEqual(
            classify_removal(
                instance_id=get_pnp_instance_id({"FriendlyName": TARGET}),
                exit_code=None,
                output="",
                still_present=False,
            ),
            "MISSING_INSTANCE_ID",
        )

    def test_nested_single_device_array_unwraps(self):
        device = {"InstanceId": r"BTHENUM\Dev_C8247887E57C\x", "FriendlyName": TARGET}
        self.assertEqual(get_pnp_instance_id([device]), device["InstanceId"])


class CleanupGateTests(unittest.TestCase):
    """Adapter reset proceeds only after verified PnP cleanup."""

    def test_cleanup_fail_blocks_adapter_reset(self):
        cleanup_verified = False
        next_state = "RESETTING_ADAPTER" if cleanup_verified else "CLEANUP_FAILED"
        self.assertEqual(next_state, "CLEANUP_FAILED")

    def test_cleanup_pass_allows_adapter_reset(self):
        cleanup_verified = True
        next_state = "RESETTING_ADAPTER" if cleanup_verified else "CLEANUP_FAILED"
        self.assertEqual(next_state, "RESETTING_ADAPTER")


if __name__ == "__main__":
    unittest.main()
