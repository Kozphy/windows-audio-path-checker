from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Iterable


@dataclass(frozen=True)
class Evidence:
    """A normalized, machine-readable fact used by the diagnosis engine."""

    id: str
    source: str
    kind: str
    status: str
    summary: str
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Hypothesis:
    """A ranked root-cause candidate supported by explicit evidence."""

    code: str
    title: str
    confidence: float
    severity: str
    explanation: str
    recommendation: str
    evidence_ids: tuple[str, ...]
    contradicting_evidence_ids: tuple[str, ...] = ()


_SEVERITY_WEIGHT = {"critical": 1.0, "warning": 0.72, "info": 0.35, "ok": 0.0}


def build_evidence(snapshot: dict[str, Any]) -> list[Evidence]:
    evidence: list[Evidence] = []

    for service in snapshot.get("services", []):
        name = str(service.get("name") or service.get("friendly_name") or "unknown")
        status = str(service.get("status", "unknown")).casefold()
        evidence.append(
            Evidence(
                id=f"service:{name}",
                source="windows-service-control-manager",
                kind="service-state",
                status="pass" if status == "running" else "fail",
                summary=f"{service.get('friendly_name', name)} is {status}.",
                attributes=dict(service),
            )
        )

    core_audio = snapshot.get("core_audio", {})
    endpoint = core_audio.get("default_endpoint") or {}
    if endpoint:
        evidence.append(
            Evidence(
                id="core-audio:default-endpoint",
                source="windows-core-audio",
                kind="default-endpoint",
                status="observed",
                summary=f"Windows default endpoint is {endpoint.get('name') or 'unknown'}.",
                attributes=dict(endpoint),
            )
        )

    master_volume = core_audio.get("master_volume")
    master_muted = core_audio.get("master_muted")
    if master_volume is not None or master_muted is not None:
        silent = bool(master_muted) or float(master_volume or 0.0) <= 0.02
        evidence.append(
            Evidence(
                id="core-audio:master-volume",
                source="windows-core-audio",
                kind="master-volume",
                status="fail" if silent else "pass",
                summary=(
                    f"Master volume is {float(master_volume or 0.0) * 100:.0f}% "
                    f"and muted={bool(master_muted)}."
                ),
                attributes={"volume": master_volume, "muted": master_muted},
            )
        )

    portaudio = snapshot.get("portaudio", {})
    output_devices = list(portaudio.get("output_devices", []))
    evidence.append(
        Evidence(
            id="portaudio:outputs",
            source="portaudio-wasapi",
            kind="app-output-inventory",
            status="pass" if output_devices else "fail",
            summary=f"Normal apps can see {len(output_devices)} playback output(s).",
            attributes={
                "default_output_name": portaudio.get("default_output_name"),
                "count": len(output_devices),
            },
        )
    )

    for index, session in enumerate(core_audio.get("sessions", [])):
        process = str(session.get("process") or "unknown")
        muted = bool(session.get("muted"))
        volume = float(session.get("volume", 1.0) or 0.0)
        status = "fail" if muted or volume <= 0.02 else "pass"
        evidence.append(
            Evidence(
                id=f"session:{process}:{index}",
                source="windows-core-audio",
                kind="application-session",
                status=status,
                summary=(
                    f"{process} volume={volume * 100:.0f}%, muted={muted}, "
                    f"output={session.get('output_device') or 'unknown'}."
                ),
                attributes=dict(session),
            )
        )

    for index, error in enumerate(snapshot.get("errors", [])):
        evidence.append(
            Evidence(
                id=f"collector-error:{index}",
                source=str(error.get("source") or "collector"),
                kind="collector-error",
                status="unknown",
                summary=str(error.get("message") or "Unknown collector error"),
                attributes=dict(error),
            )
        )

    return evidence


def _matching_ids(evidence: Iterable[Evidence], predicate: Any) -> tuple[str, ...]:
    return tuple(item.id for item in evidence if predicate(item))


def rank_hypotheses(
    snapshot: dict[str, Any], evidence: list[Evidence] | None = None
) -> list[Hypothesis]:
    """Convert deterministic findings into ranked, explainable RCA candidates."""

    evidence = evidence or build_evidence(snapshot)
    findings = list(snapshot.get("findings", []))
    hypotheses: list[Hypothesis] = []

    for finding in findings:
        severity = str(finding.get("severity", "info"))
        if severity == "ok":
            continue

        code = str(finding.get("code", "unknown"))
        supporting: tuple[str, ...] = ()
        contradicting: tuple[str, ...] = ()
        confidence = _SEVERITY_WEIGHT.get(severity, 0.35)

        if code == "browser-session-silent":
            supporting = _matching_ids(
                evidence,
                lambda item: item.kind == "application-session"
                and item.attributes.get("is_browser")
                and item.status == "fail",
            )
            contradicting = _matching_ids(
                evidence,
                lambda item: item.kind == "master-volume" and item.status == "fail",
            )
            confidence = 0.97 if supporting else 0.82
        elif code == "browser-output-mismatch":
            supporting = _matching_ids(
                evidence,
                lambda item: item.kind == "application-session"
                and item.attributes.get("is_browser")
                and bool(item.attributes.get("output_device")),
            )
            confidence = 0.90 if supporting else 0.74
        elif code in {"master-muted", "audio-service-stopped", "no-output-devices"}:
            kind = {
                "master-muted": "master-volume",
                "audio-service-stopped": "service-state",
                "no-output-devices": "app-output-inventory",
            }[code]
            supporting = _matching_ids(
                evidence, lambda item: item.kind == kind and item.status == "fail"
            )
            confidence = 0.99 if supporting else 0.85
        elif code == "default-device-mismatch":
            supporting = tuple(
                item.id
                for item in evidence
                if item.kind in {"default-endpoint", "app-output-inventory"}
            )
            confidence = 0.84
        elif code == "browser-session-missing":
            supporting = _matching_ids(
                evidence,
                lambda item: item.kind == "application-session"
                and item.attributes.get("is_browser"),
            )
            confidence = 0.58 if not supporting else 0.35
        elif code == "partial-scan":
            supporting = _matching_ids(
                evidence, lambda item: item.kind == "collector-error"
            )
            confidence = 0.68
        else:
            supporting = tuple(item.id for item in evidence if item.status == "fail")

        confidence = max(0.0, min(1.0, confidence - 0.08 * len(contradicting)))
        hypotheses.append(
            Hypothesis(
                code=code,
                title=str(finding.get("title", code)),
                confidence=round(confidence, 2),
                severity=severity,
                explanation=str(finding.get("detail", "")),
                recommendation=str(finding.get("action", "")),
                evidence_ids=supporting,
                contradicting_evidence_ids=contradicting,
            )
        )

    return sorted(
        hypotheses,
        key=lambda item: (
            -item.confidence,
            -_SEVERITY_WEIGHT.get(item.severity, 0.0),
            item.code,
        ),
    )


def build_diagnosis(snapshot: dict[str, Any]) -> dict[str, Any]:
    evidence = build_evidence(snapshot)
    hypotheses = rank_hypotheses(snapshot, evidence)
    return {
        "engine_version": 1,
        "evidence": [asdict(item) for item in evidence],
        "hypotheses": [asdict(item) for item in hypotheses],
        "primary_hypothesis": asdict(hypotheses[0]) if hypotheses else None,
        "summary": {
            "evidence_count": len(evidence),
            "hypothesis_count": len(hypotheses),
            "critical_hypothesis_count": sum(
                1 for item in hypotheses if item.severity == "critical"
            ),
            "scan_complete": not any(
                item.kind == "collector-error" for item in evidence
            ),
        },
    }
