"""Bluetooth candidate classification, ranking, and pairing diagnostics."""

from .candidates import (
    PAIRABILITY_UNKNOWN,
    PAIRABLE,
    NOT_PAIRABLE,
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
from .state import PairState

__all__ = [
    "PAIRABILITY_UNKNOWN",
    "PAIRABLE",
    "NOT_PAIRABLE",
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
]
