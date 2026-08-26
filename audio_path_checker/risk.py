from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


_IMPACT_BY_CAUSE: dict[str, float] = {
    "windows-audio-service": 0.9,
    "playback-device-unavailable": 0.85,
    "bluetooth-adapter-disabled": 0.75,
    "bluetooth-state-desync": 0.65,
    "default-output-routing": 0.55,
    "browser-output-routing": 0.45,
    "browser-session-muted": 0.35,
    "browser-session-not-observed": 0.25,
    "insufficient-observability": 0.6,
}


@dataclass(frozen=True)
class RiskAssessment:
    cause_code: str
    probability: float
    impact: float
    score: float
    level: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def risk_level(score: float) -> str:
    if score >= 0.70:
        return "critical"
    if score >= 0.45:
        return "high"
    if score >= 0.20:
        return "medium"
    return "low"


def assess_root_cause(root_cause: dict[str, Any]) -> RiskAssessment:
    """Convert diagnostic confidence into an auditable operational risk score.

    Probability comes from the inference layer. Impact is intentionally kept in
    a separate lookup so diagnosis and business/operational policy remain
    independently reviewable.
    """
    code = str(root_cause.get("code") or "unknown")
    probability = min(1.0, max(0.0, float(root_cause.get("probability") or 0.0)))
    impact = _IMPACT_BY_CAUSE.get(code, 0.4)
    score = round(probability * impact, 4)
    return RiskAssessment(
        cause_code=code,
        probability=round(probability, 4),
        impact=impact,
        score=score,
        level=risk_level(score),
    )


def assess_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    inference = snapshot.get("inference") or {}
    causes = inference.get("root_causes") or []
    assessments = [assess_root_cause(cause) for cause in causes]
    assessments.sort(key=lambda item: item.score, reverse=True)
    return {
        "schema_version": 1,
        "method": "probability-times-impact",
        "assessments": [item.to_dict() for item in assessments],
        "top_risk": assessments[0].to_dict() if assessments else None,
    }
