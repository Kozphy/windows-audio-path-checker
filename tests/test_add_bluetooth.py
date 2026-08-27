"""Tests for Add Bluetooth device API and CLI wiring."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from audio_path_checker.bluetooth import (
    DEFAULT_ADD_BLUETOOTH_ADDRESS,
    DEFAULT_ADD_BLUETOOTH_NAME,
    add_bluetooth_device,
    auto_pair_script_path,
    normalize_bluetooth_address,
)
from audio_path_checker.__main__ import build_parser


class NormalizeAddressTests(unittest.TestCase):
    """MAC normalization accepts common formats and rejects invalid input."""

    def test_accepts_common_mac_forms(self):
        for value in (
            "c8247887e57c",
            "C8:24:78:87:E5:7C",
            "c8-24-78-87-e5-7c",
        ):
            self.assertEqual(normalize_bluetooth_address(value), "c8247887e57c")

    def test_rejects_invalid(self):
        """Empty, truncated, and non-hex strings raise ValueError."""
        with self.assertRaises(ValueError):
            normalize_bluetooth_address("not-a-mac")
        with self.assertRaises(ValueError):
            normalize_bluetooth_address("")
        with self.assertRaises(ValueError):
            normalize_bluetooth_address("bad")
        with self.assertRaises(ValueError):
            normalize_bluetooth_address("abcd")


class AutoPairScriptPathTests(unittest.TestCase):
    """Bundled PowerShell auto-pair script resolves from the repo layout."""

    def test_resolves_repo_script(self):
        path = auto_pair_script_path()
        self.assertEqual(path.name, "wapc-bt-auto-pair.ps1")
        self.assertTrue(path.is_file(), f"missing script at {path}")


class AddBluetoothDeviceTests(unittest.TestCase):
    """Elevated auto-pair passes target identity and validates addresses early."""

    def test_elevated_args_include_target_identity(self):
        with patch("audio_path_checker.bluetooth.sys.platform", "win32"), patch(
            "audio_path_checker.bluetooth._run_elevated_script",
            return_value=(0, "ok"),
        ) as elevated, patch(
            "audio_path_checker.bluetooth._read_auto_pair_status",
            return_value={
                "overall_result": "SUCCESS",
                "classification": "SUCCESS",
            },
        ):
            result = add_bluetooth_device(
                name="EDIFIER W800BT Pro",
                address="C8:24:78:87:E5:7C",
                elevate=True,
                diagnostics=True,
                discovery_timeout_sec=90,
            )

        self.assertTrue(result["success"])
        self.assertEqual(result["target_address"], "c8247887e57c")
        self.assertEqual(result["classification"], "SUCCESS")
        kwargs = elevated.call_args.kwargs
        extra = kwargs["extra_args"]
        self.assertIn("-TargetName", extra)
        self.assertIn("EDIFIER W800BT Pro", extra)
        self.assertIn("-TargetAddress", extra)
        self.assertIn("c8247887e57c", extra)
        self.assertIn("-DiscoveryTimeoutSec", extra)
        self.assertIn("90", extra)
        self.assertIn("-Diagnostics", extra)

    def test_invalid_address_rejected_before_launch(self):
        """Bad MAC never reaches the elevated script launcher."""
        with patch("audio_path_checker.bluetooth.sys.platform", "win32"):
            with self.assertRaises(ValueError):
                add_bluetooth_device(name="Headset", address="not-a-mac")
            with patch(
                "audio_path_checker.bluetooth._run_elevated_script"
            ) as elevated:
                with self.assertRaises(ValueError):
                    add_bluetooth_device(name="Headset", address="abcd")
                elevated.assert_not_called()


class AddBluetoothCliTests(unittest.TestCase):
    """CLI parser wires --add-bluetooth defaults and custom overrides."""

    def test_parser_defaults(self):
        args = build_parser().parse_args(["--add-bluetooth"])
        self.assertEqual(args.add_bluetooth, DEFAULT_ADD_BLUETOOTH_NAME)
        self.assertEqual(args.bluetooth_address, DEFAULT_ADD_BLUETOOTH_ADDRESS)

    def test_parser_custom_name_and_address(self):
        args = build_parser().parse_args(
            [
                "--add-bluetooth",
                "My Headset",
                "--bluetooth-address",
                "aa:bb:cc:dd:ee:ff",
            ]
        )
        self.assertEqual(args.add_bluetooth, "My Headset")
        self.assertEqual(args.bluetooth_address, "aa:bb:cc:dd:ee:ff")


if __name__ == "__main__":
    unittest.main()
