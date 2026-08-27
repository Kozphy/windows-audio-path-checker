"""SQLite persistence for scan snapshots and timeline transitions.

Stores enriched diagnostic snapshots and semantic state transitions so local
runs can be queried for trends (top root causes, critical-scan ratio) without
re-parsing raw JSON files on every read.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


_SCHEMA = """
CREATE TABLE IF NOT EXISTS scans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    fingerprint TEXT,
    top_root_cause TEXT,
    top_confidence REAL,
    has_critical INTEGER NOT NULL,
    snapshot_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_scans_created_at ON scans(created_at);
CREATE INDEX IF NOT EXISTS idx_scans_root_cause ON scans(top_root_cause);

CREATE TABLE IF NOT EXISTS transitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observed_at TEXT NOT NULL,
    event_type TEXT NOT NULL,
    field TEXT,
    code TEXT,
    before_json TEXT,
    after_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_transitions_observed_at ON transitions(observed_at);
CREATE INDEX IF NOT EXISTS idx_transitions_type ON transitions(event_type);
"""


def connect(path: str | Path) -> sqlite3.Connection:
    """Open (or create) a WAL-mode database with the scans/transitions schema.

    Args:
        path: Filesystem location for the SQLite database file. Parent
            directories are created when missing.

    Returns:
        An open connection with schema applied and foreign keys enabled.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(target)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(_SCHEMA)
    return connection


def store_snapshot(connection: sqlite3.Connection, snapshot: dict[str, Any], *, fingerprint: str | None = None) -> int:
    """Persist one scan snapshot and index its top root cause.

    Args:
        connection: Active database connection from :func:`connect`.
        snapshot: Enriched scan dict (typically includes ``inference`` and
            ``findings``). Full JSON is stored in ``snapshot_json``.
        fingerprint: Optional stable state hash from
            :func:`audio_path_checker.timeline.state_fingerprint`.

    Returns:
        The auto-generated row id of the inserted scan.
    """
    inference = snapshot.get("inference") or {}
    top = inference.get("top_root_cause") or {}
    findings = snapshot.get("findings") or []
    created_at = str(snapshot.get("created_at") or "")
    cursor = connection.execute(
        """
        INSERT INTO scans(created_at, fingerprint, top_root_cause, top_confidence, has_critical, snapshot_json)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            created_at,
            fingerprint,
            top.get("code"),
            float(top.get("probability") or 0.0) if top else None,
            int(any(item.get("severity") == "critical" for item in findings)),
            json.dumps(snapshot, ensure_ascii=False, sort_keys=True, default=str),
        ),
    )
    connection.commit()
    return int(cursor.lastrowid)


def store_timeline(connection: sqlite3.Connection, timeline: dict[str, Any]) -> tuple[int, int]:
    """Bulk-insert all samples and transitions from a recorded timeline.

    Args:
        connection: Active database connection.
        timeline: Output of :func:`audio_path_checker.timeline.record_timeline`
            containing ``samples`` and ``transitions`` lists.

    Returns:
        Tuple of ``(scan_count, transition_count)`` rows inserted.
    """
    scan_count = 0
    transition_count = 0
    for sample in timeline.get("samples") or []:
        store_snapshot(
            connection,
            sample.get("snapshot") or {},
            fingerprint=str(sample.get("fingerprint") or "") or None,
        )
        scan_count += 1

    for event in timeline.get("transitions") or []:
        connection.execute(
            """
            INSERT INTO transitions(observed_at, event_type, field, code, before_json, after_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                str(event.get("observed_at") or ""),
                str(event.get("type") or "unknown"),
                event.get("field"),
                event.get("code"),
                json.dumps(event.get("before"), ensure_ascii=False, default=str),
                json.dumps(event.get("after"), ensure_ascii=False, default=str),
            ),
        )
        transition_count += 1
    connection.commit()
    return scan_count, transition_count


def summary(connection: sqlite3.Connection) -> dict[str, Any]:
    """Aggregate scan and transition statistics for dashboard or CLI display.

    Args:
        connection: Active database connection with prior stored data.

    Returns:
        Dict with total scan/transition counts, critical-scan ratio, and the
        five most frequent ``top_root_cause`` codes.
    """
    scans, critical = connection.execute(
        "SELECT COUNT(*), COALESCE(SUM(has_critical), 0) FROM scans"
    ).fetchone()
    transitions = connection.execute("SELECT COUNT(*) FROM transitions").fetchone()[0]
    top_causes = connection.execute(
        """
        SELECT top_root_cause, COUNT(*) AS occurrences
        FROM scans
        WHERE top_root_cause IS NOT NULL
        GROUP BY top_root_cause
        ORDER BY occurrences DESC, top_root_cause ASC
        LIMIT 5
        """
    ).fetchall()
    return {
        "scan_count": int(scans),
        "critical_scan_count": int(critical),
        "critical_scan_ratio": round(int(critical) / int(scans), 4) if scans else 0.0,
        "transition_count": int(transitions),
        "top_root_causes": [
            {"code": code, "occurrences": int(count)} for code, count in top_causes
        ],
    }
