"""Path invariants checked against collected evidence.

Invariants encode consistency rules that must hold when the audio path is
healthy (for example, a connected device should expose A2DP or a media node).
They complement :func:`~.classifier.classify_state` by surfacing cross-signal
contradictions—cases where individual features look plausible but the combined
evidence violates expected Windows Bluetooth→audio stack relationships.

Violations feed evaluation metrics, UI warnings, and future ML/LLM providers
without changing the primary state label.
"""

from __future__ import annotations

from typing import Any

from ..collectors.evidence import evidence_feature_vector


def check_invariants(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    """Evaluate path invariants against evidence features.

    Each invariant is conditional: it is only checked when its ``only_if``
    predicate is true (for example, endpoint rules apply only when an endpoint
    node was reported).

    Args:
        evidence: Normalized evidence document.

    Returns:
        List of invariant result dicts, each with keys ``invariant``,
        ``expected``, ``observed``, ``satisfied``, and ``severity`` (``info``
        when satisfied, otherwise ``high`` or ``medium``).

    Notes:
        Satisfied checks are included with ``severity="info"`` so callers can
        audit the full invariant trail, not only failures.
    """
    f = evidence_feature_vector(evidence)
    results: list[dict[str, Any]] = []

    def _inv(
        name: str,
        expected: bool,
        observed: bool,
        *,
        severity: str = "high",
        only_if: bool = True,
    ) -> None:
        if not only_if:
            return
        ok = expected == observed
        results.append(
            {
                "invariant": name,
                "expected": expected,
                "observed": observed,
                "satisfied": ok,
                "severity": "info" if ok else severity,
            }
        )

    _inv(
        "connected_device_requires_a2dp_or_media",
        True,
        bool(f["a2dp_present"] or f["media_node_present"]),
        only_if=f["device_connected"],
        severity="high",
    )
    _inv(
        "connected_device_requires_audio_endpoint",
        True,
        bool(f["endpoint_present"]),
        only_if=f["device_connected"],
        severity="high",
    )
    _inv(
        "media_node_implies_audio_endpoint",
        True,
        bool(f["endpoint_present"]),
        only_if=f["media_node_present"],
        severity="high",
    )
    _inv(
        "active_endpoint_is_routable",
        True,
        bool(f["endpoint_active"]),
        only_if=f["endpoint_present"],
        severity="medium",
    )
    _inv(
        "healthy_path_uses_default_when_known",
        True,
        bool(f["is_default_playback"]),
        only_if=f["endpoint_active"]
        and evidence.get("audio", {}).get("is_default_playback") is not None,
        severity="medium",
    )
    _inv(
        "bluetooth_connected_implies_adapter_enabled",
        True,
        bool(f["adapter_enabled"]),
        only_if=f["device_connected"],
        severity="high",
    )
    return results
