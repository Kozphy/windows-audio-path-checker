from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from audio_path_checker.storage import connect, store_snapshot, store_timeline, summary


class StorageTests(unittest.TestCase):
    def test_store_snapshot_and_summary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.db"
            with connect(path) as connection:
                scan_id = store_snapshot(
                    connection,
                    {
                        "created_at": "2026-08-26T00:00:00+00:00",
                        "findings": [{"severity": "critical", "code": "master-muted"}],
                        "inference": {
                            "top_root_cause": {
                                "code": "master-output-muted",
                                "probability": 0.99,
                            }
                        },
                    },
                    fingerprint="abc",
                )
                self.assertGreater(scan_id, 0)
                result = summary(connection)
                self.assertEqual(result["scan_count"], 1)
                self.assertEqual(result["critical_scan_count"], 1)
                self.assertEqual(result["top_root_causes"][0]["code"], "master-output-muted")

    def test_store_timeline_persists_transitions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.db"
            with connect(path) as connection:
                scans, transitions = store_timeline(
                    connection,
                    {
                        "samples": [
                            {
                                "fingerprint": "one",
                                "snapshot": {
                                    "created_at": "2026-08-26T00:00:00+00:00",
                                    "findings": [],
                                    "inference": {},
                                },
                            }
                        ],
                        "transitions": [
                            {
                                "observed_at": "2026-08-26T00:00:05+00:00",
                                "type": "finding-opened",
                                "code": "browser-session-silent",
                            }
                        ],
                    },
                )
                self.assertEqual((scans, transitions), (1, 1))
                self.assertEqual(summary(connection)["transition_count"], 1)


if __name__ == "__main__":
    unittest.main()
