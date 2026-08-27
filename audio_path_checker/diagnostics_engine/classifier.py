"""Deterministic audio-path state classification from evidence.

Maps normalized evidence features to a single :class:`~..models.states.AudioPathState`.
Classification is **priority-ordered**, not a literal walk of
:data:`~..models.states.PATH_TRANSITIONS`:

1. Radio missing/disabled → ``RADIO_UNAVAILABLE``
2. Explicitly observed Audiosrv / AudioEndpointBuilder failure →
   ``AUDIO_SERVICE_FAILURE`` (can preempt later Bluetooth stages)
3. Then paired → connected → A2DP → media → endpoint → active → default

``PATH_TRANSITIONS`` is documentation/evaluation metadata and includes later
app-session / actual-output stages that this classifier does not assign today.
"""

from __future__ import annotations

from typing import Any

from ..collectors.evidence import evidence_feature_vector
from ..models.states import AudioPathState


def classify_state(evidence: dict[str, Any]) -> dict[str, Any]:
    """Classify Bluetooth audio-path state from collected evidence.

    Precedence: adapter availability first; then an **observed** audio-service
    failure may win before pairing/connectivity checks; otherwise the path is
    evaluated as paired → connected → A2DP → media node → endpoint → active →
    default route. The first failing check in that order wins.

    Args:
        evidence: Normalized evidence document from
            :func:`~..collectors.evidence.collect_evidence`.

    Returns:
        Classification dict with keys:

        * ``state`` — :class:`~..models.states.AudioPathState` value string
        * ``confidence`` — float in ``[0, 1]``
        * ``evidence_ids`` — tags explaining the decision
        * ``features`` — feature vector used for the decision
        * ``failed_transition`` — transition label where the path broke, or
          ``None`` for healthy/unknown

    Notes:
        ``AUDIO_SERVICE_FAILURE`` is only assigned when service status was
        explicitly observed as non-running (not ``unknown``). Bluetooth
        "connected" alone does not imply audio is working. Feature booleans
        collapse missing/unknown to ``False`` via ``evidence_feature_vector``.
    """
    f = evidence_feature_vector(evidence)
    evidence_ids: list[str] = []

    def _hit(state: AudioPathState, confidence: float, ids: list[str]) -> dict[str, Any]:
        return {
            "state": state.value,
            "confidence": round(confidence, 2),
            "evidence_ids": ids,
            "features": f,
            "failed_transition": _failed_transition(state),
        }

    if not f["adapter_present"] or not f["adapter_enabled"]:
        evidence_ids = ["adapter_missing_or_disabled"]
        return _hit(AudioPathState.RADIO_UNAVAILABLE, 0.95, evidence_ids)

    audio_svc = f["audio_services_healthy"]
    if not audio_svc and (
        str((evidence.get("services") or {}).get("Audiosrv", "")).casefold()
        not in {"unknown", ""}
    ):
        # Only classify service failure when we actually observed non-running.
        aud = str((evidence.get("services") or {}).get("Audiosrv", "")).casefold()
        aeb = str(
            (evidence.get("services") or {}).get("AudioEndpointBuilder", "")
        ).casefold()
        if aud not in {"running", "unknown"} or aeb not in {"running", "unknown"}:
            return _hit(
                AudioPathState.AUDIO_SERVICE_FAILURE,
                0.9,
                ["audio_service_not_running"],
            )

    if not f["device_paired"]:
        return _hit(AudioPathState.DEVICE_NOT_PAIRED, 0.9, ["device_not_paired"])

    if not f["device_connected"]:
        return _hit(
            AudioPathState.PAIRED_NOT_CONNECTED, 0.88, ["paired_but_not_connected"]
        )

    if not f["a2dp_present"] and not f["media_node_present"]:
        return _hit(
            AudioPathState.CONNECTED_NO_A2DP,
            0.86,
            ["connected", "a2dp_missing", "media_missing"],
        )

    if f["a2dp_present"] and not f["media_node_present"]:
        return _hit(
            AudioPathState.A2DP_NO_MEDIA_NODE,
            0.84,
            ["a2dp_present", "media_missing"],
        )

    if f["media_node_present"] and not f["endpoint_present"]:
        return _hit(
            AudioPathState.MEDIA_NO_ENDPOINT,
            0.92,
            ["connected", "media_present", "endpoint_missing"],
        )

    if f["endpoint_present"] and not f["endpoint_active"]:
        return _hit(
            AudioPathState.ENDPOINT_DISABLED,
            0.9,
            ["endpoint_present", "endpoint_inactive"],
        )

    if (
        f["endpoint_active"]
        and f.get("is_default_playback") is False
        and evidence.get("audio", {}).get("is_default_playback") is False
    ):
        return _hit(
            AudioPathState.ENDPOINT_NOT_DEFAULT,
            0.8,
            ["endpoint_active", "not_default_route"],
        )

    if f["endpoint_active"] and f["audio_services_healthy"]:
        return _hit(
            AudioPathState.AUDIO_PATH_HEALTHY,
            0.93,
            ["endpoint_active", "audio_services_running"],
        )

    # Connected-looking but incomplete path without clear media/endpoint signals
    if f["device_connected"] and not f["endpoint_present"]:
        return _hit(
            AudioPathState.MEDIA_NO_ENDPOINT,
            0.75,
            ["connected", "endpoint_missing"],
        )

    return _hit(AudioPathState.UNKNOWN, 0.4, ["insufficient_evidence"])


def _failed_transition(state: AudioPathState) -> str | None:
    """Map a classified state to the transition label that failed.

    Args:
        state: Classified audio-path state.

    Returns:
        Human-readable ``from→to`` transition string, or ``None`` when the
        path is healthy or indeterminate.
    """
    mapping = {
        AudioPathState.RADIO_UNAVAILABLE: "physical_device→bluetooth_radio",
        AudioPathState.DEVICE_NOT_PAIRED: "bluetooth_radio→paired",
        AudioPathState.PAIRED_NOT_CONNECTED: "paired→connected",
        AudioPathState.CONNECTED_NO_A2DP: "connected→a2dp_profile",
        AudioPathState.A2DP_NO_MEDIA_NODE: "a2dp_profile→pnp_media_node",
        AudioPathState.MEDIA_NO_ENDPOINT: "pnp_media_node→audio_endpoint",
        AudioPathState.ENDPOINT_DISABLED: "audio_endpoint→endpoint_active",
        AudioPathState.ENDPOINT_NOT_DEFAULT: "endpoint_active→default_playback_route",
        AudioPathState.AUDIO_SERVICE_FAILURE: "default_playback_route→windows_audio_engine",
        AudioPathState.AUDIO_PATH_HEALTHY: None,
        AudioPathState.UNKNOWN: None,
        AudioPathState.WINRT_DISCOVERY_UNAVAILABLE: "discovery_capability",
    }
    return mapping.get(state)
