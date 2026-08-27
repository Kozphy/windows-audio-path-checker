"""Tests for audio-path timeline fingerprints, diffs, and reliability metrics."""

from __future__ import annotations

import unittest
from audio_path_checker.timeline import compact_state, diff_states, state_fingerprint, timeline_metrics


def snapshot(*, muted: bool = False, finding: str = "browser-session-visible", endpoint: str = "Headphones") -> dict:
    """Build a minimal snapshot for timeline fingerprint and diff tests."""
    return {
        "created_at": "2026-08-26T00:00:00+00:00",
        "core_audio": {
            "default_endpoint": {"name": endpoint, "id": "endpoint-1"},
            "master_muted": False,
            "master_volume": 0.75,
            "sessions": [
                {
                    "process": "chrome.exe",
                    "pid": 42,
                    "is_browser": True,
                    "muted": muted,
                    "volume": 0.6,
                    "state": "active",
                    "output_device": endpoint,
                }
            ],
        },
        "portaudio": {"default_output_name": endpoint},
        "bluetooth": {"default_endpoint_present": True},
        "findings": [{"code": finding, "severity": "critical" if muted else "ok"}],
    }


class TimelineTests(unittest.TestCase):
    """State fingerprints, diffs, and metrics over repeated samples."""

    def test_fingerprint_is_stable_for_equivalent_state(self) -> None:
        self.assertEqual(state_fingerprint(snapshot()), state_fingerprint(snapshot()))

    def test_compact_state_keeps_browser_path(self) -> None:
        state = compact_state(snapshot())
        self.assertEqual(state["endpoint"], "Headphones")
        self.assertEqual(state["browser_sessions"][0]["process"], "chrome.exe")

    def test_diff_reports_opened_and_resolved_findings(self) -> None:
        before = snapshot(muted=False, finding="browser-session-visible")
        after = snapshot(muted=True, finding="browser-session-silent")
        events = diff_states(before, after)
        self.assertIn({"type": "finding-opened", "code": "browser-session-silent"}, events)
        self.assertIn({"type": "finding-resolved", "code": "browser-session-visible"}, events)
        self.assertTrue(any(event["type"] == "browser-session-change" for event in events))

    def test_metrics_measure_critical_ratio(self) -> None:
        normal = snapshot()
        critical = snapshot(muted=True, finding="browser-session-silent")
        samples = [
            {"fingerprint": state_fingerprint(normal), "snapshot": normal},
            {"fingerprint": state_fingerprint(critical), "snapshot": critical},
        ]
        metrics = timeline_metrics(samples, [{"type": "finding-opened"}])
        self.assertEqual(metrics["sample_count"], 2)
        self.assertEqual(metrics["unique_states"], 2)
        self.assertEqual(metrics["critical_sample_ratio"], 0.5)


if __name__ == "__main__":
    unittest.main()
