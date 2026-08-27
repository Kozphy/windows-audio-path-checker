"""Deterministic diagnosis from collected evidence.

The diagnostics engine turns a normalized evidence document into an explicit
``AudioPathState``, invariant checks, and ranked root-cause hypotheses.

State classification walks the physical→playback path (radio → paired →
connected → A2DP → media node → endpoint → active → default route). The
first failed transition wins, producing a single state with confidence and
evidence tags.

Invariants encode cross-signal consistency rules (for example, a connected
device should expose A2DP or a media node). They complement classification by
surfacing contradictions that a single transition check might miss, and they
feed evaluation and future ML/LLM providers with structured violation records.

Public API:
    classify_state — map evidence → ``AudioPathState``
    check_invariants — validate expected relationships in evidence
    rank_hypotheses — deterministic root-cause ranking from state + evidence
"""

from .classifier import classify_state
from .invariants import check_invariants
from .root_cause import rank_hypotheses

__all__ = ["classify_state", "check_invariants", "rank_hypotheses"]
