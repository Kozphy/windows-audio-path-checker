"""Explainable diagnostic confidence (not calibrated probability).

Score framework:
  base
  + corroborating direct signals
  + unique identity
  + fresh consistent inventory
  - conflicts / ghosts / ambiguous identity / missing required signals
"""

from __future__ import annotations

from typing import Any


def estimate_confidence(
    *,
    graph: dict[str, Any],
    state: str,
    evidence_ids: list[str],
) -> dict[str, Any]:
    """Compute an explainable diagnostic confidence score for a classification."""
    flags = graph.get("flags") or {}
    target = graph.get("target") or {}
    supporting: list[str] = []
    contradictions: list[str] = []
    score = 0.45

    if target.get("canonical_bluetooth_address"):
        score += 0.12
        supporting.append("target Bluetooth address uniquely present on device node")
    else:
        score -= 0.08
        contradictions.append("target address missing — identity weaker")

    if flags.get("paired"):
        score += 0.08
        supporting.append("paired BTHENUM/device node observed")

    if state == "PAIRED_NOT_CONNECTED":
        if flags.get("connected") is False and not flags.get("inventory_present"):
            score += 0.22
            supporting.append("no A2DP/MEDIA/AudioEndpoint descendants for target")
            supporting.append("connected signal is false")
            if flags.get("ghost_inventory"):
                score -= 0.05
                contradictions.append("ghost inventory for another MAC present (ignored)")
        elif flags.get("inventory_present"):
            score -= 0.15
            contradictions.append("inventory present while connected=false — may be stale")

    if state == "STALE_PNP_INVENTORY":
        score += 0.18
        supporting.append("identity-matched audio inventory while connected=false")
        supporting.append("treat inventory as stale/ghost relative to link state")

    if state in {
        "PROFILE_ENUMERATION_PENDING",
        "ENDPOINT_ENUMERATION_PENDING",
    }:
        score += 0.1
        supporting.append("settling window active — absence treated as pending")
        if graph.get("elapsed_ms") is not None:
            supporting.append(f"elapsed_ms={graph.get('elapsed_ms')}")

    if state == "AUDIO_PATH_HEALTHY":
        score += 0.3
        supporting.extend(
            [
                "endpoint active",
                "audio services healthy",
            ]
        )

    if state == "AUDIO_SERVICE_FAILURE":
        score += 0.2
        supporting.append("Audiosrv/AudioEndpointBuilder observed non-running")

    if state == "IDENTITY_AMBIGUOUS":
        score = min(score, 0.4)
        contradictions.append("multiple conflicting identity candidates")

    if state == "INSUFFICIENT_EVIDENCE":
        score = min(score, 0.35)
        contradictions.append("required signals missing")

    for tag in evidence_ids:
        if tag not in supporting:
            supporting.append(tag)

    score = max(0.05, min(0.99, score))
    if score >= 0.85:
        label = "high"
    elif score >= 0.65:
        label = "medium"
    else:
        label = "low"

    return {
        "confidence_score": round(score, 2),
        "confidence_label": label,
        "supporting_evidence": supporting,
        "contradictions": contradictions,
        "note": "diagnostic confidence score (explainable weights; not calibrated probability)",
    }
