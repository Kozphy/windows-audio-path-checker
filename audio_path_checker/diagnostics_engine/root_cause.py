"""Rule-based root-cause hypothesis ranking.

Produces a deterministic, confidence-sorted list of likely causes given
classified state and evidence. Missing MEDIA/endpoints while disconnected is
**not** stale PnP — that requires identity-matched inventory without a live link.
"""

from __future__ import annotations

from typing import Any

from ..models.states import AudioPathState


def rank_hypotheses(
    evidence: dict[str, Any],
    classification: dict[str, Any],
) -> list[dict[str, Any]]:
    """Rank root-cause hypotheses from evidence and classification."""
    state = str(classification.get("state") or AudioPathState.UNKNOWN.value)
    graph = classification.get("evidence_graph") or {}
    flags = graph.get("flags") or {}
    caps = evidence.get("capabilities") or {}
    hypotheses: list[dict[str, Any]] = []

    def _add(cause: str, confidence: float, evidence_tags: list[str]) -> None:
        hypotheses.append(
            {
                "cause": cause,
                "confidence": round(confidence, 2),
                "evidence": evidence_tags,
            }
        )

    if not caps.get("available", True) and caps.get("reason"):
        _add(
            "winrt_discovery_failure",
            0.95,
            ["winrt_unavailable", str(caps.get("reason"))],
        )

    if state == AudioPathState.RADIO_UNAVAILABLE.value:
        _add("bluetooth_adapter_disabled", 0.95, ["adapter_disabled_or_missing"])
    elif state == AudioPathState.DEVICE_NOT_PAIRED.value:
        _add("device_not_paired", 0.9, ["no_bt_device_node"])
    elif state == AudioPathState.IDENTITY_AMBIGUOUS.value:
        _add("identity_ambiguous", 0.9, ["conflicting_identity_candidates"])
    elif state == AudioPathState.STALE_PNP_INVENTORY.value:
        _add(
            "stale_pnp_state",
            0.86,
            ["not_connected", "identity_matched_inventory_present"],
        )
        _add(
            "bluetooth_device_disconnected",
            0.4,
            ["paired", "not_connected"],
        )
    elif state == AudioPathState.PAIRED_NOT_CONNECTED.value:
        # Paired BTHENUM OK + no descendants ⇒ genuine idle/disconnected.
        _add(
            "bluetooth_device_disconnected",
            0.9 if not flags.get("inventory_present") else 0.55,
            ["paired", "not_connected", "no_live_audio_profile"],
        )
        _add(
            "bluetooth_connection_failure",
            0.4,
            ["paired", "not_connected"],
        )
    elif state in {
        AudioPathState.PROFILE_ENUMERATION_PENDING.value,
        AudioPathState.ENDPOINT_ENUMERATION_PENDING.value,
    }:
        _add(
            "enumeration_settling",
            0.8,
            ["connected", "awaiting_asynchronous_pnp"],
        )
        _add(
            "a2dp_profile_initialization_failure",
            0.35,
            ["possible_timeout_if_settle_exhausts"],
        )
    elif state in {
        AudioPathState.CONNECTED_NO_A2DP.value,
        AudioPathState.A2DP_NO_MEDIA_NODE.value,
    }:
        _add(
            "a2dp_profile_initialization_failure",
            0.78,
            ["bluetooth_connected", "a2dp_or_media_missing"],
        )
        _add(
            "stale_pnp_state",
            0.45,
            ["connected_without_profile_stack"],
        )
    elif state == AudioPathState.MEDIA_NO_ENDPOINT.value:
        _add(
            "audio_endpoint_enumeration_failure",
            0.82 if flags.get("media_present") else 0.72,
            [
                "bluetooth_connected",
                "audio_endpoint_missing",
                "audio_services_running"
                if flags.get("audio_services_running")
                else "audio_services_unknown",
            ],
        )
        _add(
            "a2dp_profile_initialization_failure",
            0.4,
            ["connected", "endpoint_missing"],
        )
    elif state == AudioPathState.ENDPOINT_DISABLED.value:
        _add("endpoint_disabled", 0.85, ["endpoint_present", "endpoint_inactive"])
    elif state == AudioPathState.ENDPOINT_NOT_DEFAULT.value:
        _add("wrong_default_output", 0.88, ["endpoint_active", "not_default"])
    elif state == AudioPathState.AUDIO_SERVICE_FAILURE.value:
        _add(
            "audio_service_failure",
            0.9,
            ["audiosrv_or_endpoint_builder_unhealthy"],
        )
    elif state == AudioPathState.AUDIO_PATH_HEALTHY.value:
        _add("none", 0.95, ["path_healthy"])
    elif state == AudioPathState.INSUFFICIENT_EVIDENCE.value:
        _add("insufficient_evidence", 0.35, ["required_signals_missing"])

    if not hypotheses:
        _add("unknown", 0.3, ["insufficient_evidence"])

    hypotheses.sort(key=lambda h: h["confidence"], reverse=True)
    return hypotheses
