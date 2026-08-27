"""Explicit Bluetooth → Windows audio path states.

Each :class:`AudioPathState` is the primary decision label consumed by
classifiers, remediation planners, and CLI exit handling.

:data:`PATH_TRANSITIONS` is **documentation / evaluation metadata** for the
ideal physical→playback chain. It is not walked at runtime by
:func:`~audio_path_checker.diagnostics_engine.classifier.classify_state`, and it
includes later stages (application session, actual output) that the classifier
does not currently emit as states.
"""

from __future__ import annotations

from enum import Enum


class AudioPathState(str, Enum):
    """Discrete states along the Bluetooth headset audio path.

    Values progress from hardware/radio problems toward healthy playback.
    Classification assigns exactly one state per evidence snapshot.
    """

    UNKNOWN = "UNKNOWN"
    RADIO_UNAVAILABLE = "RADIO_UNAVAILABLE"
    DEVICE_NOT_PAIRED = "DEVICE_NOT_PAIRED"
    PAIRED_NOT_CONNECTED = "PAIRED_NOT_CONNECTED"
    CONNECTED_NO_A2DP = "CONNECTED_NO_A2DP"
    A2DP_NO_MEDIA_NODE = "A2DP_NO_MEDIA_NODE"
    MEDIA_NO_ENDPOINT = "MEDIA_NO_ENDPOINT"
    ENDPOINT_DISABLED = "ENDPOINT_DISABLED"
    ENDPOINT_NOT_DEFAULT = "ENDPOINT_NOT_DEFAULT"
    AUDIO_SERVICE_FAILURE = "AUDIO_SERVICE_FAILURE"
    AUDIO_PATH_HEALTHY = "AUDIO_PATH_HEALTHY"
    WINRT_DISCOVERY_UNAVAILABLE = "WINRT_DISCOVERY_UNAVAILABLE"


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
