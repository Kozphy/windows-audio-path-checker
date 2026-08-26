from __future__ import annotations

from typing import Any

from .audit import create_record
from .inference import enrich_snapshot
from .policy import evaluate_snapshot
from .risk import assess_snapshot


def build_decision(snapshot: dict[str, Any], *, event_id: str = "decision") -> dict[str, Any]:
    """Create a reviewable decision envelope from a diagnostic snapshot."""
    enriched = enrich_snapshot(snapshot)
    risk = assess_snapshot(enriched)
    policy = evaluate_snapshot(risk)
    payload = {
        "inference": enriched.get("inference"),
        "risk": risk,
        "policy": policy,
    }
    audit = create_record(event_id, "decision", payload)
    return {
        "schema_version": 1,
        "snapshot": enriched,
        "risk": risk,
        "policy": policy,
        "audit": audit.to_dict(),
    }
