"""Evaluation utilities for reproducible benchmark cases.

This module intentionally separates per-case scoring from aggregate reporting and
never fabricates benchmark values. Aggregate metrics are computed only from case
records supplied by an experiment runner.
"""

from __future__ import annotations

from math import sqrt
from typing import Any, Callable, Iterable


METRIC_NAMES = (
    "Root Cause Accuracy",
    "State Accuracy",
    "Repair Success Rate",
    "Unsafe Action Rate",
    "Unnecessary Reset Rate",
    "False Success Rate",
    "MTTD",
    "MTTR",
    "Evidence Collection Latency",
    "Recovery Verification Rate",
)

STRATEGIES = (
    "naive_reset",
    "rule_based_diagnosis",
    "single_signal",
    "state_aware_diagnosis",
    "future_ml_llm_agent",
)


def empty_metrics_table() -> dict[str, dict[str, None]]:
    """Return an explicitly empty benchmark table for unmeasured strategies."""
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
    """Score one case against known ground truth without inventing aggregates."""
    diagnosis = diagnose_fn(evidence)
    classification = diagnosis.get("classification") or {}
    hypotheses = diagnosis.get("hypotheses") or []
    top = hypotheses[0] if hypotheses else {}
    state_ok = expected_state is None or str(classification.get("state")) == expected_state
    cause_ok = expected_cause is None or str(top.get("cause")) == expected_cause
    unsafe = bool(forbidden_actions and planned_action in forbidden_actions)
    return {
        "state_match": state_ok,
        "cause_match": cause_ok,
        "unsafe_action": unsafe,
        "predicted_state": classification.get("state"),
        "predicted_cause": top.get("cause"),
        "metrics_note": "Aggregate metrics require real case records.",
    }


def proportion_ci(successes: int, total: int, z: float = 1.96) -> dict[str, float | int | None]:
    """Wilson score interval for a binomial proportion.

    The default z=1.96 corresponds approximately to a 95% confidence interval.
    Counts are returned with the estimate so reports retain denominator context.
    """
    if total < 0 or successes < 0 or successes > total:
        raise ValueError("Require 0 <= successes <= total")
    if total == 0:
        return {"successes": successes, "total": total, "estimate": None, "low": None, "high": None}

    p = successes / total
    z2 = z * z
    denominator = 1 + z2 / total
    center = (p + z2 / (2 * total)) / denominator
    margin = (z / denominator) * sqrt((p * (1 - p) / total) + (z2 / (4 * total * total)))
    return {
        "successes": successes,
        "total": total,
        "estimate": p,
        "low": max(0.0, center - margin),
        "high": min(1.0, center + margin),
    }


def aggregate_cases(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate non-excluded benchmark records into transparent metrics.

    Expected optional booleans include state_match, cause_match, unsafe_action,
    unnecessary_reset, recovery_attempted, recovery_verified, and false_success.
    Missing fields are excluded from that metric's denominator rather than guessed.
    """
    all_records = list(records)
    kept = [record for record in all_records if not record.get("excluded", False)]

    def metric(field: str) -> dict[str, float | int | None]:
        observed = [record[field] for record in kept if isinstance(record.get(field), bool)]
        return proportion_ci(sum(value is True for value in observed), len(observed))

    recovery_records = [
        record for record in kept
        if record.get("recovery_attempted") is True and isinstance(record.get("recovery_verified"), bool)
    ]
    recovery_successes = sum(record["recovery_verified"] is True for record in recovery_records)

    return {
        "trials": len(kept),
        "excluded_trials": len(all_records) - len(kept),
        "state_accuracy": metric("state_match"),
        "root_cause_accuracy": metric("cause_match"),
        "unsafe_action_rate": metric("unsafe_action"),
        "unnecessary_reset_rate": metric("unnecessary_reset"),
        "false_success_rate": metric("false_success"),
        "recovery_success_rate": proportion_ci(recovery_successes, len(recovery_records)),
    }
