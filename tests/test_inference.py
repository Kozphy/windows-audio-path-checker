"""Tests for root-cause inference layered on diagnostic findings."""

from __future__ import annotations

import unittest
from audio_path_checker.inference import enrich_snapshot, infer_root_causes


class InferenceTests(unittest.TestCase):
    """Ranked root causes reflect finding severity without inventing new ones."""

    def test_browser_mute_is_ranked_high(self) -> None:
        snapshot = {
            "findings": [
                {"severity": "ok", "code": "audio-services-running", "detail": "ok"},
                {"severity": "ok", "code": "app-output-available", "detail": "Speakers"},
                {
                    "severity": "critical",
                    "code": "browser-session-silent",
                    "detail": "chrome.exe (0%), muted",
                },
            ]
        }
        causes = infer_root_causes(snapshot)
        self.assertEqual(causes[0]["code"], "browser-session-muted")
        self.assertGreaterEqual(causes[0]["probability"], 0.95)
        self.assertEqual(causes[0]["confidence"], "high")
        self.assertGreaterEqual(len(causes[0]["evidence"]), 2)

    def test_output_mismatch_is_explainable(self) -> None:
        snapshot = {
            "findings": [
                {
                    "severity": "warning",
                    "code": "browser-output-mismatch",
                    "detail": "Chrome on Speakers; default headphones",
                }
            ]
        }
        cause = infer_root_causes(snapshot)[0]
        self.assertEqual(cause["code"], "browser-output-routing")
        self.assertIn("Volume mixer", cause["recommendation"])
        self.assertTrue(cause["evidence"][0].startswith("browser-output-mismatch:"))

    def test_unknown_findings_do_not_create_fake_causes(self) -> None:
        snapshot = {"findings": [{"severity": "info", "code": "future-code"}]}
        self.assertEqual(infer_root_causes(snapshot), [])

    def test_enrich_snapshot_preserves_original_fields(self) -> None:
        snapshot = {"schema_version": 4, "findings": []}
        enriched = enrich_snapshot(snapshot)
        self.assertEqual(enriched["schema_version"], 4)
        self.assertIn("inference", enriched)
        self.assertIsNone(enriched["inference"]["top_root_cause"])
        self.assertNotIn("inference", snapshot)


if __name__ == "__main__":
    unittest.main()
