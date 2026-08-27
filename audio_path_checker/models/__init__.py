"""Shared domain models for audio-path diagnosis.

Defines explicit ``AudioPathState`` / ``CheckStatus`` values, path maturity,
failure taxonomy, and the ordered transition graph along the Bluetooth →
Windows playback path.
"""

from .states import (
    FAILURE_TAXONOMY,
    PATH_MATURITY,
    PATH_TRANSITIONS,
    AudioPathState,
    CheckStatus,
)

__all__ = [
    "AudioPathState",
    "CheckStatus",
    "PATH_TRANSITIONS",
    "PATH_MATURITY",
    "FAILURE_TAXONOMY",
]
