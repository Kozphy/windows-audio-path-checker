"""Bluetooth candidate classification, ranking, and pairing diagnostics.

Public API for the Python side of auto-pair: identity filtering (address over
name), deterministic candidate ranking, pairability tri-state, failure taxonomy,
and recovery state invariants.

Notes:
    **Bluetooth connected ≠ audio working.** Pairing or PnP ``OK`` status does
    not prove Core Audio / A2DP endpoints exist; use ``classify_outcome`` and
    ``check_recovery_invariants`` together with orchestrator stage results.

    **Pairability vs discovery:** A device may appear in discovery
    (``any_bluetooth_device_discovered``) without being pairable
    (``CanPair=False``). ``PAIRABILITY_UNKNOWN`` means enumeration failed, not
    that the target is absent.
"""

from .candidates import (
    PAIRABILITY_UNKNOWN,
    PAIRABLE,
    NOT_PAIRABLE,
    build_rank_result,
    classify_candidate,
    determine_pairability,
    group_candidates_by_physical_device,
    rank_candidates,
    score_candidate,
    score_candidate_with_components,
    select_pairable_candidate,
    update_candidate_history,
)
from .failures import FailureReason, classify_outcome, map_pair_status
from .identity import (
    build_target_identity,
    check_recovery_invariants,
    exit_code_for_classification,
    filter_candidates_by_identity,
    match_bluetooth_identity,
    normalize_bluetooth_address,
    pnp_node_matches_target,
)
from .state import PairState

__all__ = [
    "PAIRABILITY_UNKNOWN",
    "PAIRABLE",
    "NOT_PAIRABLE",
    "build_rank_result",
    "classify_candidate",
    "determine_pairability",
    "group_candidates_by_physical_device",
    "rank_candidates",
    "score_candidate",
    "score_candidate_with_components",
    "select_pairable_candidate",
    "update_candidate_history",
    "FailureReason",
    "classify_outcome",
    "map_pair_status",
    "PairState",
    "build_target_identity",
    "check_recovery_invariants",
    "exit_code_for_classification",
    "filter_candidates_by_identity",
    "match_bluetooth_identity",
    "normalize_bluetooth_address",
    "pnp_node_matches_target",
]
