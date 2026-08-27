"""Tests for the non-elevated audio inventory refresh."""

from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from audio_path_checker.remediation.refresh import refresh_audio_endpoint_inventory


class RefreshAudioEndpointInventoryTests(unittest.TestCase):
    def test_refresh_queries_only_media_and_audioendpoint_classes(self):
        completed = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        with (
            patch("audio_path_checker.remediation.refresh.sys.platform", "win32"),
            patch(
                "audio_path_checker.remediation.refresh.subprocess.run",
                return_value=completed,
            ) as run,
            patch("audio_path_checker.remediation.refresh.time.sleep") as sleep,
        ):
            result = refresh_audio_endpoint_inventory(settle_seconds=0.25)

        command = run.call_args.args[0]
        script = command[-1]
        self.assertIn("Get-PnpDevice", script)
        self.assertIn("MEDIA", script)
        self.assertIn("AudioEndpoint", script)
        self.assertIn("Bluetooth", script)
        for mutating_command in (
            "Disable-PnpDevice",
            "Enable-PnpDevice",
            "pnputil",
            "Restart-Service",
            "Remove-PnpDevice",
        ):
            self.assertNotIn(mutating_command, script)
        self.assertTrue(result["attempted"])
        self.assertTrue(result["command_succeeded"])
        sleep.assert_called_once_with(0.25)

    def test_refresh_is_not_attempted_off_windows(self):
        with (
            patch("audio_path_checker.remediation.refresh.sys.platform", "linux"),
            patch("audio_path_checker.remediation.refresh.subprocess.run") as run,
        ):
            result = refresh_audio_endpoint_inventory()

        self.assertFalse(result["attempted"])
        self.assertFalse(result["command_succeeded"])
        self.assertEqual(result["detail"], "unsupported_platform")
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
