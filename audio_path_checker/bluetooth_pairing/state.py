"""Bluetooth auto-pair state machine labels.

Ordered stages from cleanup through verification. State names describe
orchestrator progress — ``PAIRED`` / ``VERIFIED`` require identity-checked
audio path proof, not merely a Bluetooth link in Settings.
"""

from __future__ import annotations

from enum import Enum


class PairState(str, Enum):
    """High-level auto-pair orchestrator states.

    Members mirror the PowerShell state machine. Terminal states include
    ``VERIFIED`` (audio path OK for target identity), ``FAILED``, and
    ``TIMEOUT``.

    Notes:
        ``AUDIO_ENDPOINT_WAIT`` / ``AUDIO_ENDPOINT_READY`` exist because
        pairing can succeed while Core Audio endpoints are still enumerating —
        **Bluetooth connected ≠ audio working**.
    """

    INIT = "INIT"
    CLEANING_GHOST_PAIR = "CLEANING_GHOST_PAIR"
    RESETTING_ADAPTER = "RESETTING_ADAPTER"
    RESTARTING_SERVICES = "RESTARTING_SERVICES"
    WAITING_FOR_DEVICE_PAIRING_MODE = "WAITING_FOR_DEVICE_PAIRING_MODE"
    DISCOVERING = "DISCOVERING"
    CANDIDATE_FOUND = "CANDIDATE_FOUND"
    PAIRABLE_CANDIDATE_FOUND = "PAIRABLE_CANDIDATE_FOUND"
    PAIR_REQUESTED = "PAIR_REQUESTED"
    PAIRING = "PAIRING"
    PAIRED = "PAIRED"
    AUDIO_ENDPOINT_WAIT = "AUDIO_ENDPOINT_WAIT"
    AUDIO_ENDPOINT_READY = "AUDIO_ENDPOINT_READY"
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"
