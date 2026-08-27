"""Explicit Bluetooth → Windows audio path states and check statuses.

Each :class:`AudioPathState` is the primary decision label consumed by
classifiers, remediation planners, and CLI exit handling.

:data:`PATH_TRANSITIONS` is **documentation / evaluation metadata** for the
ideal physical→playback chain. It is not walked at runtime by
:func:`~audio_path_checker.diagnostics_engine.classifier.classify_state`.

:data:`PATH_MATURITY` orders progressive path completeness for closed-loop
recovery (monotonic progress detection).

Invariants (enforced by tests + classifier/planner):

* A — never classify ``AUDIO_PATH_HEALTHY`` without verified usable path signals
* B — ghost MEDIA/endpoint for a different MAC must never credit the target
* C — R1 refresh must not mutate pairing/registry/adapter/PnP removal
* D — recovery success requires postconditions, not command exit code alone
* E — identity survives address formatting, ghosts, and FriendlyName collisions
* F — classifier is deterministic for the same evidence snapshot
* G — unknown evidence stays UNKNOWN (not coerced to FAIL) in check statuses
"""

from __future__ import annotations

from enum import Enum


class CheckStatus(str, Enum):
    """Per-check display / audit status (finer than a boolean)."""

    PASS = "PASS"
    FAIL = "FAIL"
    PENDING = "PENDING"
    UNKNOWN = "UNKNOWN"
    STALE = "STALE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class AudioPathState(str, Enum):
    """Discrete states along the Bluetooth headset audio path.

    Values progress from hardware/radio problems toward healthy playback.
    Classification assigns exactly one state per evidence snapshot (optionally
    influenced by a settling context for temporal PENDING states).
    """

    UNKNOWN = "UNKNOWN"
    RADIO_UNAVAILABLE = "RADIO_UNAVAILABLE"
    DEVICE_NOT_PAIRED = "DEVICE_NOT_PAIRED"
    IDENTITY_AMBIGUOUS = "IDENTITY_AMBIGUOUS"
    PAIRED_NOT_CONNECTED = "PAIRED_NOT_CONNECTED"
    STALE_PNP_INVENTORY = "STALE_PNP_INVENTORY"
    PROFILE_ENUMERATION_PENDING = "PROFILE_ENUMERATION_PENDING"
    CONNECTED_NO_A2DP = "CONNECTED_NO_A2DP"
    A2DP_NO_MEDIA_NODE = "A2DP_NO_MEDIA_NODE"
    ENDPOINT_ENUMERATION_PENDING = "ENDPOINT_ENUMERATION_PENDING"
    MEDIA_NO_ENDPOINT = "MEDIA_NO_ENDPOINT"
    ENDPOINT_DISABLED = "ENDPOINT_DISABLED"
    ENDPOINT_NOT_DEFAULT = "ENDPOINT_NOT_DEFAULT"
    AUDIO_SERVICE_FAILURE = "AUDIO_SERVICE_FAILURE"
    AUDIO_PATH_HEALTHY = "AUDIO_PATH_HEALTHY"
    WINRT_DISCOVERY_UNAVAILABLE = "WINRT_DISCOVERY_UNAVAILABLE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


# Ordered ``(from_stage, to_stage)`` pairs from physical device to actual output.
PATH_TRANSITIONS: tuple[tuple[str, str], ...] = (
    ("physical_device", "bluetooth_radio"),
    ("bluetooth_radio", "paired"),
    ("paired", "connected"),
    ("connected", "a2dp_profile"),
    ("a2dp_profile", "pnp_media_node"),
    ("pnp_media_node", "audio_endpoint"),
    ("audio_endpoint", "endpoint_active"),
    ("endpoint_active", "default_playback_route"),
    ("default_playback_route", "windows_audio_engine"),
    ("windows_audio_engine", "application_session"),
    ("application_session", "actual_output"),
)

# Monotonic maturity ranks for closed-loop recovery progress.
PATH_MATURITY: dict[str, int] = {
    AudioPathState.UNKNOWN.value: 0,
    AudioPathState.INSUFFICIENT_EVIDENCE.value: 0,
    AudioPathState.RADIO_UNAVAILABLE.value: 0,
    AudioPathState.DEVICE_NOT_PAIRED.value: 0,
    AudioPathState.IDENTITY_AMBIGUOUS.value: 0,
    AudioPathState.WINRT_DISCOVERY_UNAVAILABLE.value: 0,
    AudioPathState.AUDIO_SERVICE_FAILURE.value: 0,
    AudioPathState.PAIRED_NOT_CONNECTED.value: 1,
    AudioPathState.STALE_PNP_INVENTORY.value: 1,
    AudioPathState.PROFILE_ENUMERATION_PENDING.value: 2,
    AudioPathState.CONNECTED_NO_A2DP.value: 2,
    AudioPathState.A2DP_NO_MEDIA_NODE.value: 3,
    AudioPathState.ENDPOINT_ENUMERATION_PENDING.value: 4,
    AudioPathState.MEDIA_NO_ENDPOINT.value: 4,
    AudioPathState.ENDPOINT_DISABLED.value: 5,
    AudioPathState.ENDPOINT_NOT_DEFAULT.value: 5,
    AudioPathState.AUDIO_PATH_HEALTHY.value: 6,
}

FAILURE_TAXONOMY: dict[str, str] = {
    AudioPathState.RADIO_UNAVAILABLE.value: "BLUETOOTH_RADIO",
    AudioPathState.DEVICE_NOT_PAIRED.value: "PAIRING",
    AudioPathState.IDENTITY_AMBIGUOUS.value: "IDENTITY",
    AudioPathState.PAIRED_NOT_CONNECTED.value: "LINK",
    AudioPathState.STALE_PNP_INVENTORY.value: "PNP_ENUMERATION",
    AudioPathState.PROFILE_ENUMERATION_PENDING.value: "PROFILE_NEGOTIATION",
    AudioPathState.CONNECTED_NO_A2DP.value: "PROFILE_NEGOTIATION",
    AudioPathState.A2DP_NO_MEDIA_NODE.value: "PNP_ENUMERATION",
    AudioPathState.ENDPOINT_ENUMERATION_PENDING.value: "AUDIO_ENDPOINT",
    AudioPathState.MEDIA_NO_ENDPOINT.value: "AUDIO_ENDPOINT",
    AudioPathState.ENDPOINT_DISABLED.value: "AUDIO_ENDPOINT",
    AudioPathState.ENDPOINT_NOT_DEFAULT.value: "ROUTING",
    AudioPathState.AUDIO_SERVICE_FAILURE.value: "WINDOWS_AUDIO",
    AudioPathState.WINRT_DISCOVERY_UNAVAILABLE.value: "PROFILE_NEGOTIATION",
    AudioPathState.INSUFFICIENT_EVIDENCE.value: "UNKNOWN",
    AudioPathState.UNKNOWN.value: "UNKNOWN",
    AudioPathState.AUDIO_PATH_HEALTHY.value: "NONE",
}
