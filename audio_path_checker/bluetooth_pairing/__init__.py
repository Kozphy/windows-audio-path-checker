"""Bluetooth candidate classification, ranking, and pairing diagnostics."""

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
