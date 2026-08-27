"""Deterministic audio-path state classification from evidence.

Maps an evidence graph to a single :class:`~..models.states.AudioPathState`.
Classification is **priority-ordered**:

1. Radio missing/disabled → ``RADIO_UNAVAILABLE``
2. Observed Audiosrv / AudioEndpointBuilder failure → ``AUDIO_SERVICE_FAILURE``
3. Identity / pairing / link / profile / media / endpoint path

Optional settle context (``settling=True``) maps early post-connect gaps to
``PROFILE_ENUMERATION_PENDING`` / ``ENDPOINT_ENUMERATION_PENDING`` instead of
hard FAIL states.

Invariant F: same evidence (+ settle flags) ⇒ same state.
Invariant G: check statuses may be UNKNOWN; feature booleans may still collapse
missing→False for path decisions — use ``checks`` for CLI display.
"""

from __future__ import annotations

from typing import Any

from ..collectors.evidence import evidence_feature_vector
from ..models.states import FAILURE_TAXONOMY, PATH_MATURITY, AudioPathState
from .confidence import estimate_confidence
from .evidence_graph import build_evidence_graph


def classify_state(
    evidence: dict[str, Any],
    *,
    settling: bool = False,
    elapsed_ms: int | None = None,
) -> dict[str, Any]:
    """Classify Bluetooth audio-path state from collected evidence.

    Args:
        evidence: Normalized evidence document.
        settling: When True, treat connected-but-incomplete path as PENDING.
        elapsed_ms: Optional settle elapsed time for audit.

    Returns:
        Classification dict including ``state``, ``confidence``,
        ``confidence_detail``, ``checks``, ``evidence_graph``, ``maturity``,
        ``failure_category``, ``features``, ``failed_transition``.
    """
    graph = build_evidence_graph(
        evidence, settling=settling, elapsed_ms=elapsed_ms
    )
    flags = graph["flags"]
    f = evidence_feature_vector(evidence)
    # Prefer identity-correlated flags over raw bool collapse.
    f = {
        **f,
        "device_paired": flags["paired"],
        "device_connected": flags["connected"],
        "a2dp_present": flags["a2dp_present"],
        "media_node_present": flags["media_present"],
        "endpoint_present": flags["endpoint_present"],
        "endpoint_active": flags["endpoint_active"],
    }

    def _hit(
        state: AudioPathState,
        confidence: float,
        ids: list[str],
    ) -> dict[str, Any]:
        detail = estimate_confidence(
            graph=graph, state=state.value, evidence_ids=ids
        )
        # Prefer explainable score when available; keep legacy key for compat.
        conf = float(detail.get("confidence_score") or confidence)
        return {
            "state": state.value,
            "confidence": round(conf, 2),
            "confidence_detail": detail,
            "evidence_ids": ids,
            "features": f,
            "failed_transition": _failed_transition(state),
            "checks": graph["checks"],
            "evidence_graph": graph,
            "maturity": PATH_MATURITY.get(state.value, graph.get("maturity", 0)),
            "failure_category": FAILURE_TAXONOMY.get(state.value, "UNKNOWN"),
            "settling": settling,
        }

    if not f["adapter_present"] or not f["adapter_enabled"]:
        return _hit(AudioPathState.RADIO_UNAVAILABLE, 0.95, ["adapter_missing_or_disabled"])

    aud = str((evidence.get("services") or {}).get("Audiosrv", "")).casefold()
    aeb = str(
        (evidence.get("services") or {}).get("AudioEndpointBuilder", "")
    ).casefold()
    # Only classify service failure when status was explicitly observed.
    if aud not in {"unknown", ""} and (
        aud not in {"running", "unknown"} or aeb not in {"running", "unknown", ""}
    ):
        if aud != "running" or (aeb and aeb not in {"running", "unknown", ""}):
            return _hit(
                AudioPathState.AUDIO_SERVICE_FAILURE,
                0.9,
                ["audio_service_not_running"],
            )

    if not f["device_paired"]:
        return _hit(AudioPathState.DEVICE_NOT_PAIRED, 0.9, ["device_not_paired"])

    if not f["device_connected"]:
        if flags.get("inventory_present"):
            return _hit(
                AudioPathState.STALE_PNP_INVENTORY,
                0.82,
                [
                    "paired",
                    "not_connected",
                    "identity_matched_inventory_present",
                ],
            )
        return _hit(
            AudioPathState.PAIRED_NOT_CONNECTED,
            0.88,
            ["paired_but_not_connected", "no_live_audio_profile"],
        )

    # Connected path
    if not f["a2dp_present"] and not f["media_node_present"]:
        if settling:
            return _hit(
                AudioPathState.PROFILE_ENUMERATION_PENDING,
                0.75,
                ["connected", "awaiting_a2dp_or_media"],
            )
        return _hit(
            AudioPathState.CONNECTED_NO_A2DP,
            0.86,
            ["connected", "a2dp_missing", "media_missing"],
        )

    if f["a2dp_present"] and not f["media_node_present"]:
        if settling:
            return _hit(
                AudioPathState.PROFILE_ENUMERATION_PENDING,
                0.78,
                ["a2dp_present", "awaiting_media"],
            )
        return _hit(
            AudioPathState.A2DP_NO_MEDIA_NODE,
            0.84,
            ["a2dp_present", "media_missing"],
        )

    if f["media_node_present"] and not f["endpoint_present"]:
        if settling:
            return _hit(
                AudioPathState.ENDPOINT_ENUMERATION_PENDING,
                0.8,
                ["media_present", "awaiting_endpoint"],
            )
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

    if f["device_connected"] and not f["endpoint_present"]:
        return _hit(
            AudioPathState.MEDIA_NO_ENDPOINT,
            0.75,
            ["connected", "endpoint_missing"],
        )

    return _hit(AudioPathState.INSUFFICIENT_EVIDENCE, 0.35, ["insufficient_evidence"])


def _failed_transition(state: AudioPathState) -> str | None:
    """Map a classified state to the transition label that failed."""
    mapping = {
        AudioPathState.RADIO_UNAVAILABLE: "physical_device→bluetooth_radio",
        AudioPathState.DEVICE_NOT_PAIRED: "bluetooth_radio→paired",
        AudioPathState.IDENTITY_AMBIGUOUS: "identity_resolution",
        AudioPathState.PAIRED_NOT_CONNECTED: "paired→connected",
        AudioPathState.STALE_PNP_INVENTORY: "paired→connected(stale_inventory)",
        AudioPathState.PROFILE_ENUMERATION_PENDING: "connected→a2dp_profile(pending)",
        AudioPathState.CONNECTED_NO_A2DP: "connected→a2dp_profile",
        AudioPathState.A2DP_NO_MEDIA_NODE: "a2dp_profile→pnp_media_node",
        AudioPathState.ENDPOINT_ENUMERATION_PENDING: "pnp_media_node→audio_endpoint(pending)",
        AudioPathState.MEDIA_NO_ENDPOINT: "pnp_media_node→audio_endpoint",
        AudioPathState.ENDPOINT_DISABLED: "audio_endpoint→endpoint_active",
        AudioPathState.ENDPOINT_NOT_DEFAULT: "endpoint_active→default_playback_route",
        AudioPathState.AUDIO_SERVICE_FAILURE: "default_playback_route→windows_audio_engine",
        AudioPathState.AUDIO_PATH_HEALTHY: None,
        AudioPathState.UNKNOWN: None,
        AudioPathState.INSUFFICIENT_EVIDENCE: None,
        AudioPathState.WINRT_DISCOVERY_UNAVAILABLE: "discovery_capability",
    }
    return mapping.get(state)
