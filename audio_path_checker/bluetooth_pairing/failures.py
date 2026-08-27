"""Explicit Bluetooth auto-pair failure taxonomy."""

from __future__ import annotations

from enum import Enum


class FailureReason(str, Enum):
    INSUFFICIENT_PRIVILEGES = "INSUFFICIENT_PRIVILEGES"
    GHOST_CLEANUP_FAILED = "GHOST_CLEANUP_FAILED"
    ADAPTER_RESET_FAILED = "ADAPTER_RESET_FAILED"
    SERVICE_CONTROL_FAILED = "SERVICE_CONTROL_FAILED"
    DISCOVERY_API_UNAVAILABLE = "DISCOVERY_API_UNAVAILABLE"
    DISCOVERY_ENUMERATION_FAILED = "DISCOVERY_ENUMERATION_FAILED"
    TARGET_NOT_DISCOVERED = "TARGET_NOT_DISCOVERED"
    TARGET_IDENTITY_MISMATCH = "TARGET_IDENTITY_MISMATCH"
    TARGET_NOT_PAIRABLE = "TARGET_NOT_PAIRABLE"
    CLASSIC_ENDPOINT_ENUMERATION_FAILED = "CLASSIC_ENDPOINT_ENUMERATION_FAILED"
    PAIRABILITY_UNDETERMINED = "PAIRABILITY_UNDETERMINED"
    DISCOVERABLE_NOT_PAIRABLE = "DISCOVERABLE_NOT_PAIRABLE"
    PNP_PATH_MISSING = "PNP_PATH_MISSING"
    A2DP_PATH_MISSING = "A2DP_PATH_MISSING"
    AUDIO_ENDPOINT_MISSING = "AUDIO_ENDPOINT_MISSING"
    INTERNAL_STATE_INVARIANT_FAILURE = "INTERNAL_STATE_INVARIANT_FAILURE"
    NO_CLASSIC_BT_ENDPOINT = "NO_CLASSIC_BT_ENDPOINT"
    NO_ASSOCIATION_ENDPOINT = "NO_ASSOCIATION_ENDPOINT"
    PAIR_REQUEST_FAILED = "PAIR_REQUEST_FAILED"
    PAIRING_REJECTED = "PAIRING_REJECTED"
    PAIRING_TIMEOUT = "PAIRING_TIMEOUT"
    PAIR_AUTHENTICATION_FAILED = "PAIR_AUTHENTICATION_FAILED"
    PAIRING_ALREADY_IN_PROGRESS = "PAIRING_ALREADY_IN_PROGRESS"
    DEVICE_ALREADY_PAIRED = "DEVICE_ALREADY_PAIRED"
    PNP_ENUMERATION_TIMEOUT = "PNP_ENUMERATION_TIMEOUT"
    A2DP_ENDPOINT_TIMEOUT = "A2DP_ENDPOINT_TIMEOUT"
    AUDIO_ENDPOINT_TIMEOUT = "AUDIO_ENDPOINT_TIMEOUT"
    PAIRING_SUCCEEDED_AUDIO_ENDPOINT_MISSING = "PAIRING_SUCCEEDED_AUDIO_ENDPOINT_MISSING"
    RANKER_INPUT_INVALID = "RANKER_INPUT_INVALID"
    NO_CANDIDATES = "NO_CANDIDATES"
    SERVICE_FAILURE = "SERVICE_FAILURE"
    ADAPTER_FAILURE = "ADAPTER_FAILURE"
    UNKNOWN_FAILURE = "UNKNOWN_FAILURE"
    SUCCESS = "SUCCESS"

    # Legacy aliases
    DISCOVERY_UNAVAILABLE = "DISCOVERY_API_UNAVAILABLE"
    DEVICE_NOT_FOUND = "TARGET_NOT_DISCOVERED"
    PAIR_REQUEST_REJECTED = "PAIRING_REJECTED"
    PAIR_TIMEOUT = "PAIRING_TIMEOUT"


# WinRT DevicePairingResultStatus (string forms from PowerShell)
_PAIR_STATUS_MAP: dict[str, FailureReason | None] = {
    "Paired": None,
    "AlreadyPaired": None,
    "NotReadyToPair": FailureReason.DISCOVERABLE_NOT_PAIRABLE,
    "NotPaired": FailureReason.PAIRING_REJECTED,
    "ConnectionRejected": FailureReason.PAIRING_REJECTED,
    "TooManyConnections": FailureReason.PAIRING_REJECTED,
    "AuthenticationFailure": FailureReason.PAIR_AUTHENTICATION_FAILED,
    "AuthenticationTimeout": FailureReason.PAIR_AUTHENTICATION_FAILED,
    "OperationAlreadyInProgress": FailureReason.PAIRING_ALREADY_IN_PROGRESS,
    "Failed": FailureReason.PAIR_REQUEST_FAILED,
}


def map_pair_status(status: str) -> FailureReason | None:
    normalized = (status or "").strip()
    if not normalized:
        return FailureReason.UNKNOWN_FAILURE
    for key, reason in _PAIR_STATUS_MAP.items():
        if key.lower() == normalized.lower():
            return reason
    return FailureReason.UNKNOWN_FAILURE


def classify_outcome(
    *,
    pairability: str,
    classic_enumeration_succeeded: bool,
    aep_enumeration_succeeded: bool,
    target_discovered: bool,
    pair_success: bool,
    audio_ready: bool,
    identity_mismatch: bool = False,
    invariant_violations: list | None = None,
) -> FailureReason | None:
    """Map evidence to failure classification; None means success path."""
    if invariant_violations:
        return FailureReason.INTERNAL_STATE_INVARIANT_FAILURE
    if pair_success and audio_ready:
        return None
    if pair_success and not audio_ready:
        return FailureReason.PAIRING_SUCCEEDED_AUDIO_ENDPOINT_MISSING
    if identity_mismatch and not target_discovered:
        return FailureReason.TARGET_IDENTITY_MISMATCH
    if not target_discovered:
        return FailureReason.TARGET_NOT_DISCOVERED
    if not classic_enumeration_succeeded and not aep_enumeration_succeeded:
        return FailureReason.CLASSIC_ENDPOINT_ENUMERATION_FAILED
    if pairability == "UNKNOWN":
        return FailureReason.PAIRABILITY_UNDETERMINED
    if pairability == "NOT_PAIRABLE":
        return FailureReason.DISCOVERABLE_NOT_PAIRABLE
    return FailureReason.UNKNOWN_FAILURE
