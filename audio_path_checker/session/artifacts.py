"""Diagnostic session artifact trail under ``artifacts/sessions/``.

Persists JSON evidence, diagnosis, summaries, and append-only action logs for
each diagnostic run. Dataset records are structured for future ML training
without unnecessary PII.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def new_session_dir(root: Path | None = None) -> Path:
    """Create a timestamped session directory for artifact storage.

    Args:
        root: Base directory; defaults to ``./artifacts/sessions`` under CWD.

    Returns:
        Newly created session path ``<root>/<YYYY-MM-DDTHHMMSS>/``.
    """
    base = root or Path.cwd() / "artifacts" / "sessions"
    stamp = datetime.now().strftime("%Y-%m-%dT%H%M%S")
    path = base / stamp
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_json(path: Path, payload: Any) -> None:
    """Write a JSON file with UTF-8 encoding and trailing newline.

    Args:
        path: Destination file path.
        payload: JSON-serializable object.
    """
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def append_action(session_dir: Path, record: dict[str, Any]) -> None:
    """Append one remediation action record to ``actions.jsonl``.

    Args:
        session_dir: Session directory created by :func:`new_session_dir`.
        record: JSON-serializable action/event dict (one line per call).
    """
    line = json.dumps(record, ensure_ascii=False)
    with (session_dir / "actions.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def write_session_bundle(
    session_dir: Path,
    *,
    evidence_before: dict[str, Any],
    diagnosis: dict[str, Any],
    evidence_after: dict[str, Any] | None = None,
    summary: dict[str, Any] | None = None,
    dataset_record: dict[str, Any] | None = None,
) -> None:
    """Write the standard set of session JSON artifacts.

    Args:
        session_dir: Target session directory.
        evidence_before: Pre-repair evidence document (always written).
        diagnosis: Provider/engine diagnosis output (always written).
        evidence_after: Post-repair evidence, if collected.
        summary: Human or pipeline summary dict.
        dataset_record: ML-ready case record from :func:`build_dataset_record`.
    """
    write_json(session_dir / "evidence-before.json", evidence_before)
    write_json(session_dir / "diagnosis.json", diagnosis)
    if evidence_after is not None:
        write_json(session_dir / "evidence-after.json", evidence_after)
    if summary is not None:
        write_json(session_dir / "summary.json", summary)
    if dataset_record is not None:
        write_json(session_dir / "dataset-record.json", dataset_record)


def build_dataset_record(
    *,
    case_id: str,
    symptom: str,
    features: dict[str, Any],
    predicted_root_cause: str,
    confidence: float,
    action: str | None,
    repair_success: bool | None,
    final_state: str,
) -> dict[str, Any]:
    """Build an ML-ready session record without unnecessary PII.

    Args:
        case_id: Stable identifier for the diagnostic case.
        symptom: User-reported or inferred symptom label.
        features: Feature vector (typically from evidence).
        predicted_root_cause: Top hypothesis cause string.
        confidence: Classifier or hypothesis confidence.
        action: Remediation action taken, if any.
        repair_success: Whether verification reported recovery.
        final_state: Post-repair :class:`~..models.states.AudioPathState` value.

    Returns:
        Dataset record dict suitable for ``dataset-record.json``.
    """
    return {
        "case_id": case_id,
        "symptom": symptom,
        "features": features,
        "predicted_root_cause": predicted_root_cause,
        "confidence": confidence,
        "action": action,
        "repair_success": repair_success,
        "final_state": final_state,
    }
