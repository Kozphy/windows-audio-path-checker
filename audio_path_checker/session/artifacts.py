"""Diagnostic session artifact trail under artifacts/sessions/."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def new_session_dir(root: Path | None = None) -> Path:
    base = root or Path.cwd() / "artifacts" / "sessions"
    stamp = datetime.now().strftime("%Y-%m-%dT%H%M%S")
    path = base / stamp
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def append_action(session_dir: Path, record: dict[str, Any]) -> None:
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
    """ML-ready session record without unnecessary PII."""
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
