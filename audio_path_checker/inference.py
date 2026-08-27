"""Transparent rule-based root-cause inference from scan findings.

Maps deterministic finding codes produced by :mod:`audio_path_checker.diagnostics`
into ranked, evidence-backed root-cause hypotheses. Scores are diagnostic
confidence weights derived from explicit rules—not population-calibrated
probabilities—so every ranking can be audited against the source findings.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class RootCause:
    """A single ranked hypothesis explaining why playback may be silent.

    Attributes:
        code: Stable machine identifier for the root cause (e.g.
            ``browser-output-routing``).
        title: Short human-readable summary.
        probability: Rule-derived confidence score in ``[0, 1]``.
        confidence: Qualitative band (``high`` | ``medium`` | ``low``).
        evidence: Supporting finding strings tying the cause to scan output.
        recommendation: Actionable next step for the user or operator.
    """

    code: str
    title: str
    probability: float
    confidence: str
    evidence: tuple[str, ...]
    recommendation: str

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-friendly dict with evidence as a list."""
        payload = asdict(self)
        payload["evidence"] = list(self.evidence)
        return payload


_WEIGHTS: dict[str, tuple[str, str, float, str]] = {
    "browser-session-silent": (
        "browser-session-muted",
        "Browser session is muted or effectively silent",
        0.96,
        "Unmute the browser session and raise its app volume, then rescan.",
    ),
    "browser-output-mismatch": (
        "browser-output-routing",
        "Browser is routed to a different playback endpoint",
        0.93,
        "Set the browser output to Default or the intended headphones in Volume mixer.",
    ),
    "default-device-mismatch": (
        "default-output-routing",
        "Windows and app audio paths disagree on the default output",
        0.84,
        "Align the Windows default endpoint and the app output, then verify playback.",
    ),
    "audio-service-stopped": (
        "windows-audio-service",
        "A Windows audio service is stopped",
        0.98,
        "Restart Windows Audio and Audio Endpoint Builder, then rescan.",
    ),
    "master-muted": (
        "master-output-muted",
        "The default playback endpoint is muted or near zero",
        0.99,
        "Unmute the endpoint and raise master volume before testing apps again.",
    ),
    "no-output-devices": (
        "playback-device-unavailable",
        "Normal applications cannot see a usable playback device",
        0.95,
        "Reconnect the device and repair or update the audio driver.",
    ),
    "bluetooth-adapter-disabled": (
        "bluetooth-adapter-disabled",
        "Bluetooth radio is disabled",
        0.99,
        "Enable the Bluetooth adapter, then reconnect or re-pair the headset.",
    ),
    "bluetooth-audio-ui-desync": (
        "bluetooth-state-desync",
        "Bluetooth pairing state and audio endpoint state are inconsistent",
        0.82,
        "Repair pairing state, reboot, re-pair, and verify the endpoint again.",
    ),
    "browser-session-missing": (
        "browser-session-not-observed",
        "No active browser audio session was observed during the scan",
        0.58,
        "Start playback in the affected browser and rescan while audio is active.",
    ),
    "partial-scan": (
        "insufficient-observability",
        "The scan did not collect all expected evidence",
        0.55,
        "Resolve the collector errors and repeat the scan before making a strong diagnosis.",
    ),
}

_SUPPORTING_OK = {
    "audio-services-running": "Windows audio services are healthy",
    "master-volume-ok": "Master endpoint volume is available and unmuted",
    "app-output-available": "Applications can see at least one playback device",
    "browser-session-visible": "Windows can observe the browser audio session",
}


def _confidence(probability: float) -> str:
    """Map a rule-derived score to ``high`` / ``medium`` / ``low``.

    Thresholds: ``>= 0.90`` → high, ``>= 0.75`` → medium, else low.
    These bands label diagnostic weight, not calibrated probabilities.
    """
    if probability >= 0.90:
        return "high"
    if probability >= 0.75:
        return "medium"
    return "low"


def infer_root_causes(snapshot: dict[str, Any], *, limit: int = 3) -> list[dict[str, Any]]:
    """Turn deterministic findings into ranked, evidence-backed hypotheses.

    Each finding code with a registered weight becomes a :class:`RootCause`.
    Routing-related causes receive additional supporting evidence when lower
    layers (services, endpoints) appear healthy, slightly boosting confidence.

    Args:
        snapshot: Scan payload containing a ``findings`` list from
            :func:`audio_path_checker.diagnostics.analyze_snapshot`.
        limit: Maximum number of hypotheses to return (at least one when any
            cause exists).

    Returns:
        List of root-cause dicts sorted by descending ``probability``, each
        suitable for JSON serialization.

    Notes:
        Probabilities are diagnostic confidence scores, not population-calibrated
        statistical probabilities. They remain transparent and rule-derived so
        every score can be audited against the originating findings.
    """
    findings = list(snapshot.get("findings") or [])
    by_code = {str(item.get("code")): item for item in findings}
    supporting = [text for code, text in _SUPPORTING_OK.items() if code in by_code]

    causes: list[RootCause] = []
    seen: set[str] = set()
    for finding in findings:
        code = str(finding.get("code") or "")
        spec = _WEIGHTS.get(code)
        if spec is None:
            continue
        cause_code, title, probability, recommendation = spec
        if cause_code in seen:
            continue
        seen.add(cause_code)

        evidence = [f"{code}: {finding.get('detail') or finding.get('title') or ''}".strip()]
        # Healthy lower layers make a routing/session-level diagnosis stronger.
        if code in {"browser-session-silent", "browser-output-mismatch", "default-device-mismatch"}:
            evidence.extend(supporting[:3])
            if "audio-services-running" in by_code and "app-output-available" in by_code:
                probability = min(0.99, probability + 0.02)

        causes.append(
            RootCause(
                code=cause_code,
                title=title,
                probability=round(probability, 3),
                confidence=_confidence(probability),
                evidence=tuple(evidence),
                recommendation=recommendation,
            )
        )

    causes.sort(key=lambda item: item.probability, reverse=True)
    return [item.to_dict() for item in causes[: max(1, limit)]]


def enrich_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Attach V2 inference metadata without mutating the collector contract.

    Args:
        snapshot: Raw or analyzed scan dict from
            :func:`audio_path_checker.diagnostics.collect_snapshot`.

    Returns:
        Shallow copy of ``snapshot`` with an added ``inference`` block containing
        ``root_causes``, ``top_root_cause``, method identifier, and disclaimer.
    """
    enriched = dict(snapshot)
    root_causes = infer_root_causes(snapshot)
    enriched["inference"] = {
        "schema_version": 1,
        "method": "transparent-rule-weighting",
        "root_causes": root_causes,
        "top_root_cause": root_causes[0] if root_causes else None,
        "disclaimer": (
            "Scores represent rule-derived diagnostic confidence and are not "
            "population-calibrated probabilities."
        ),
    }
    return enriched
