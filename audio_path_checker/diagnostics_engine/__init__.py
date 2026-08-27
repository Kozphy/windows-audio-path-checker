"""Deterministic diagnosis from collected evidence.

Public API:
    classify_state — map evidence → ``AudioPathState`` (+ checks / graph)
    check_invariants — validate expected relationships in evidence
    rank_hypotheses — deterministic root-cause ranking from state + evidence
    build_evidence_graph — identity-correlated observation graph
"""

from .classifier import classify_state
from .evidence_graph import build_evidence_graph, normalize_bluetooth_address
from .invariants import check_invariants
from .root_cause import rank_hypotheses

__all__ = [
    "classify_state",
    "check_invariants",
    "rank_hypotheses",
    "build_evidence_graph",
    "normalize_bluetooth_address",
]
