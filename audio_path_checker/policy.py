from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class PolicyDecision:
    decision: str
    reason: str
    requires_human_approval: bool
    auto_remediation_allowed: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_policy(risk: dict[str, Any]) -> PolicyDecision:
    """Map an operational risk assessment to a deterministic control decision."""
    level = str(risk.get("level") or "low").lower()
    score = float(risk.get("score") or 0.0)

    if level == "critical" or score >= 0.70:
        return PolicyDecision(
            decision="deny_auto_remediation",
            reason="Critical risk requires explicit operator review before action.",
            requires_human_approval=True,
            auto_remediation_allowed=False,
        )
    if level == "high" or score >= 0.45:
        return PolicyDecision(
            decision="require_approval",
            reason="High-risk remediation must be approved by an operator.",
            requires_human_approval=True,
            auto_remediation_allowed=False,
        )
    if level == "medium" or score >= 0.20:
        return PolicyDecision(
            decision="recommend",
            reason="Medium-risk incidents receive a recommendation but no automatic action.",
            requires_human_approval=False,
            auto_remediation_allowed=False,
        )
    return PolicyDecision(
        decision="observe",
        reason="Low-risk incidents remain under observation.",
        requires_human_approval=False,
        auto_remediation_allowed=False,
    )


def evaluate_snapshot(risk_payload: dict[str, Any]) -> dict[str, Any]:
    top_risk = risk_payload.get("top_risk")
    if not top_risk:
        return {
            "schema_version": 1,
            "decision": "observe",
            "reason": "No ranked root cause is available.",
            "requires_human_approval": False,
            "auto_remediation_allowed": False,
        }
    decision = evaluate_policy(top_risk)
    return {"schema_version": 1, **decision.to_dict()}
