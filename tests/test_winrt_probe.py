"""Tests for WinRT capability probe JSON parsing."""

from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, patch

from audio_path_checker.platform.winrt import (
    _extract_json_object,
    probe_winrt_capabilities,
)


class ExtractJsonTests(unittest.TestCase):
    def test_pure_json(self):
        raw = '{"available": true}'
        self.assertEqual(_extract_json_object(raw), raw)

    def test_mixed_report_then_json(self):
        raw = (
            "=== DISCOVERY CAPABILITY CHECK ===\n"
            "WinRT                     AVAILABLE\n\n"
            '{"available":true,"bluetooth_discovery_available":true}'
        )
        extracted = _extract_json_object(raw)
        self.assertIsNotNone(extracted)
        parsed = json.loads(extracted)
        self.assertTrue(parsed["available"])

    def test_empty(self):
        self.assertIsNone(_extract_json_object(""))


class ProbeParsingTests(unittest.TestCase):
    def test_maps_bluetooth_discovery_available_field(self):
        payload = {
            "available": True,
            "bluetooth_discovery_available": True,
            "capabilities": [
                {
                    "capability": "bluetooth_discovery",
                    "available": True,
                    "powershell_version": "5.1",
                }
            ],
            "powershell_version": "5.1",
        }
        completed = MagicMock()
        completed.stdout = (
            "=== DISCOVERY ===\n" + json.dumps(payload, separators=(",", ":"))
        )
        completed.stderr = ""
        completed.returncode = 0
        with patch("sys.platform", "win32"), patch(
            "audio_path_checker.platform.winrt.subprocess.run",
            return_value=completed,
        ), patch(
            "audio_path_checker.platform.winrt._scripts_root"
        ) as root:
            root.return_value.__truediv__.return_value.is_file.return_value = True
            result = probe_winrt_capabilities()
        self.assertTrue(result["available"])
        self.assertEqual(result["reason"], "")

    def test_invalid_json_still_reports_clear_reason(self):
        completed = MagicMock()
        completed.stdout = "not json at all"
        completed.stderr = ""
        completed.returncode = 0
        with patch("sys.platform", "win32"), patch(
            "audio_path_checker.platform.winrt.subprocess.run",
            return_value=completed,
        ), patch(
            "audio_path_checker.platform.winrt._scripts_root"
        ) as root:
            root.return_value.__truediv__.return_value.is_file.return_value = True
            result = probe_winrt_capabilities()
        self.assertFalse(result["available"])
        self.assertEqual(result["reason"], "empty_probe_output")


if __name__ == "__main__":
    unittest.main()
