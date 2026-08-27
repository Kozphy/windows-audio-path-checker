"""Evaluation harness scaffold — no fabricated metrics.

Defines metric names, strategy labels, and per-case scoring helpers for
comparing diagnosis/remediation approaches. Aggregate benchmark tables remain
empty until real measured runs populate them.
"""

from __future__ import annotations

from typing import Any, Callable


METRIC_NAMES = (
    "Root Cause Accuracy",
    "Repair Success Rate",
    "Unsafe Action Rate",
    "Unnecessary Reset Rate",
    "MTTD",
    "MTTR",
    "Evidence Collection Latency",
    "Recovery Verification Rate",
)

STRATEGIES = (
    "naive_reset",
    "rule_based_diagnosis",
    "state_aware_diagnosis",
    "future_ml_llm_agent",
)


def empty_metrics_table() -> dict[str, dict[str, None]]:
    """Return a skeleton metrics table for all strategies.

    Returns:
        Nested dict ``strategy → metric_name → None`` for each entry in
        :data:`STRATEGIES` and :data:`METRIC_NAMES`. Values are intentionally
        unset until real benchmark runs exist.
    """
    return {strategy: {metric: None for metric in METRIC_NAMES} for strategy in STRATEGIES}


def evaluate_case(
    *,
    evidence: dict[str, Any],
    diagnose_fn: Callable[[dict[str, Any]], dict[str, Any]],
    expected_state: str | None = None,
    expected_cause: str | None = None,
    forbidden_actions: list[str] | None = None,
    planned_action: str | None = None,
) -> dict[str, Any]:
    """Score a single synthetic or real diagnostic case.

    Args:
        evidence: Input evidence document passed to ``diagnose_fn``.
        diagnose_fn: Callable matching diagnosis provider ``diagnose`` signature.
        expected_state: Expected ``AudioPathState`` value, or ``None`` to skip.
        expected_cause: Expected top hypothesis cause, or ``None`` to skip.
        forbidden_actions: Action names that must not be planned (unsafe check).
        planned_action: Remediation action under test for unsafe detection.

    Returns:
        Per-case result with ``state_match``, ``cause_match``, ``unsafe_action``,
        predicted labels, and ``metrics_note`` (no invented aggregates).

    Notes:
        Does not compute roll-up benchmark statistics; callers aggregate
        multiple case results externally.
    """
    diagnosis = diagnose_fn(evidence)
    classification = diagnosis.get("classification") or {}
    hypotheses = diagnosis.get("hypotheses") or []
    top = hypotheses[0] if hypotheses else {}
    state_ok = (
        expected_state is None
        or str(classification.get("state")) == expected_state
    )
    cause_ok = expected_cause is None or str(top.get("cause")) == expected_cause
    unsafe = False
    if forbidden_actions and planned_action in forbidden_actions:
        unsafe = True
    return {
        "state_match": state_ok,
        "cause_match": cause_ok,
        "unsafe_action": unsafe,
        "predicted_state": classification.get("state"),
        "predicted_cause": top.get("cause"),
        "metrics_note": "Aggregate benchmarks intentionally unset until real runs exist.",
    }
