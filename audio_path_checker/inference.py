from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .bluetooth_inference import infer_bluetooth_state


@dataclass(frozen=True)
class RootCause:
    code: str
    title: str
    probability: float
    confidence: str
    evidence: tuple[str, ...]
    recommendation: str

    def to_dict(self) -> dict[str, Any]:
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
    if probability >= 0.90:
        return "high"
    if probability >= 0.75:
        return "medium"
    return "low"


def infer_root_causes(snapshot: dict[str, Any], *, limit: int = 3) -> list[dict[str, Any]]:
    """Turn deterministic findings into ranked, evidence-backed hypotheses.

    Probabilities are diagnostic confidence scores, not population-calibrated
    medical/statistical probabilities. They intentionally remain transparent
    and rule-derived so every score can be audited.
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
    """Attach inference metadata without mutating the collector contract."""
    enriched = dict(snapshot)
    root_causes = infer_root_causes(snapshot)
    enriched["inference"] = {
        "schema_version": 2,
        "method": "transparent-rule-weighting",
        "root_causes": root_causes,
        "top_root_cause": root_causes[0] if root_causes else None,
        "bluetooth_path": infer_bluetooth_state(snapshot),
        "disclaimer": (
            "Scores represent rule-derived diagnostic confidence and are not "
            "population-calibrated probabilities."
        ),
    }
    return enriched
