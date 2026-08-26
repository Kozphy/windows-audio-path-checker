"""End-to-end diagnose → plan → (optional) act → verify pipeline."""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .collectors.evidence import collect_evidence, evidence_feature_vector
from .models.states import AudioPathState
from .platform.winrt import format_capability_console, probe_winrt_capabilities
from .providers.diagnosis import DiagnosisProvider, get_default_provider
from .remediation.planner import plan_remediation
from .remediation.verification import verify_recovery
from .session.artifacts import (
    append_action,
    build_dataset_record,
    new_session_dir,
    write_session_bundle,
)


def format_diagnostic_report(
    *,
    device_name: str,
    evidence: dict[str, Any],
    diagnosis: dict[str, Any],
    plan: dict[str, Any],
) -> str:
    classification = diagnosis.get("classification") or {}
    hypotheses = diagnosis.get("hypotheses") or []
    top = hypotheses[0] if hypotheses else {}
    audio = evidence.get("audio") or {}
    bluetooth = evidence.get("bluetooth") or {}
    device = evidence.get("device") or {}
    services = evidence.get("services") or {}
    state = str(classification.get("state") or "UNKNOWN")

    def mark(ok: bool | None) -> str:
        if ok is True:
            return "PASS"
        if ok is False:
            return "FAIL"
        return "UNKNOWN"

    lines = [
        f"{device_name} — Audio Path Diagnostic",
        "",
        f"{'Bluetooth Adapter':<22} {mark(bool(bluetooth.get('adapter_enabled')))}",
        f"{'Device Paired':<22} {mark(bool(device.get('paired')))}",
        f"{'Device Connected':<22} {mark(bool(device.get('connected')))}",
        f"{'A2DP Profile':<22} {mark(bool(audio.get('a2dp_present')))}",
        f"{'MEDIA Node':<22} {mark(bool(audio.get('media_node_present')))}",
        f"{'Audio Endpoint':<22} {mark(bool(audio.get('endpoint_present')))}",
        f"{'Windows Audio':<22} {mark(str(services.get('Audiosrv','')).casefold()=='running')}",
        f"{'Default Output':<22} {mark(audio.get('is_default_playback'))}",
        "",
        "Diagnosis",
        "---------",
        f"State: {state}",
        f"Likely cause: {top.get('cause', 'unknown')}",
        f"Confidence: {int(round(float(top.get('confidence') or classification.get('confidence') or 0) * 100))}%",
        "",
        "Recommended action",
        "------------------",
    ]
    recommended = plan.get("recommended")
    if recommended:
        lines.append(str(recommended.get("action")))
        lines.append(str(recommended.get("reason")))
        lines.append(f"Risk: {recommended.get('risk')}")
    else:
        lines.append("None (observation only or path healthy).")

    caps = evidence.get("capabilities") or {}
    if caps and not caps.get("available", True):
        lines.extend(["", format_capability_console(caps)])

    violations = [
        inv
        for inv in (diagnosis.get("invariants") or [])
        if not inv.get("satisfied", True)
    ]
    if violations:
        lines.extend(["", "Invariant violations", "--------------------"])
        for inv in violations:
            lines.append(
                f"- {inv.get('invariant')}: expected={inv.get('expected')} "
                f"observed={inv.get('observed')} ({inv.get('severity')})"
            )
    return "\n".join(lines)


def run_audio_path_diagnosis(
    *,
    device_name: str = "EDIFIER W800BT Pro",
    mode: str = "diagnose",
    snapshot: dict[str, Any] | None = None,
    evidence: dict[str, Any] | None = None,
    provider: DiagnosisProvider | None = None,
    artifacts_root: Path | None = None,
    write_artifacts: bool = True,
    execute: bool = False,
) -> dict[str, Any]:
    """
    Evidence → State → Invariants → Hypotheses → Plan → (optional) Action → Verify.

    ``execute`` is reserved; destructive execution stays behind existing
    bluetooth repair helpers and explicit CLI flags.
    """
    started = time.perf_counter()
    session_dir = new_session_dir(artifacts_root) if write_artifacts else None
    provider = provider or get_default_provider()

    if evidence is None:
        evidence = collect_evidence(
            device_name=device_name,
            snapshot=snapshot,
            include_winrt_probe=True,
        )
    elif evidence.get("capabilities") is None:
        evidence = dict(evidence)
        evidence["capabilities"] = probe_winrt_capabilities()

    diagnosis = provider.diagnose(evidence)
    plan = plan_remediation(
        classification=diagnosis["classification"],
        hypotheses=diagnosis["hypotheses"],
        evidence=evidence,
        mode=mode,
    )

    actions_log: list[dict[str, Any]] = []
    evidence_after = None
    verification = None
    repair_command_succeeded = False

    # Diagnostic experiment: non-destructive refresh when endpoint missing
    recommended = plan.get("recommended") or {}
    if (
        execute
        and mode in {"repair", "aggressive-repair"}
        and recommended.get("action") == "refresh_audio_endpoint_inventory"
    ):
        before = "endpoint_missing"
        time.sleep(2)
        evidence_after = collect_evidence(
            device_name=device_name,
            snapshot=snapshot,
            include_winrt_probe=False,
        )
        after_present = bool((evidence_after.get("audio") or {}).get("endpoint_present"))
        exp = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": "refresh_audio_endpoint_inventory",
            "experiment": "refresh_audio_endpoint_inventory",
            "reason": recommended.get("reason"),
            "risk": "R1",
            "before": before,
            "after": "endpoint_present" if after_present else "endpoint_missing",
            "result": "hypothesis_supported"
            if after_present
            else "hypothesis_inconclusive",
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }
        actions_log.append(exp)
        if session_dir:
            append_action(session_dir, exp)
        diagnosis = provider.diagnose(evidence_after)
        plan = plan_remediation(
            classification=diagnosis["classification"],
            hypotheses=diagnosis["hypotheses"],
            evidence=evidence_after,
            mode=mode,
        )
        repair_command_succeeded = True
        verification = verify_recovery(
            evidence_after=evidence_after,
            repair_command_succeeded=True,
        )

    if evidence_after is None:
        evidence_after = evidence
        verification = verify_recovery(
            evidence_after=evidence_after,
            repair_command_succeeded=repair_command_succeeded,
            classification_after=diagnosis.get("classification"),
        )

    report_text = format_diagnostic_report(
        device_name=device_name,
        evidence=evidence,
        diagnosis=diagnosis,
        plan=plan,
    )

    case_id = session_dir.name if session_dir else str(uuid.uuid4())
    top = (diagnosis.get("hypotheses") or [{}])[0]
    dataset = build_dataset_record(
        case_id=case_id,
        symptom=_symptom(evidence),
        features=evidence_feature_vector(evidence),
        predicted_root_cause=str(top.get("cause") or "unknown"),
        confidence=float(top.get("confidence") or 0),
        action=(recommended or {}).get("action"),
        repair_success=verification.get("system_recovered") if verification else None,
        final_state=str(
            (diagnosis.get("classification") or {}).get("state")
            or AudioPathState.UNKNOWN.value
        ),
    )

    summary = {
        "device_name": device_name,
        "mode": mode,
        "classification": diagnosis.get("classification"),
        "recommended": plan.get("recommended"),
        "verification": verification,
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }

    if session_dir:
        write_session_bundle(
            session_dir,
            evidence_before=evidence,
            diagnosis={**diagnosis, "plan": plan},
            evidence_after=evidence_after,
            summary=summary,
            dataset_record=dataset,
        )
        for record in actions_log:
            append_action(session_dir, record)

    return {
        "session_dir": str(session_dir) if session_dir else None,
        "evidence": evidence,
        "evidence_after": evidence_after,
        "diagnosis": diagnosis,
        "plan": plan,
        "verification": verification,
        "report_text": report_text,
        "dataset_record": dataset,
        "summary": summary,
    }


def _symptom(evidence: dict[str, Any]) -> str:
    device = evidence.get("device") or {}
    audio = evidence.get("audio") or {}
    if device.get("connected") and not audio.get("endpoint_present"):
        return "bluetooth_connected_no_audio"
    if not device.get("paired"):
        return "device_not_paired"
    if not (evidence.get("bluetooth") or {}).get("adapter_enabled"):
        return "bluetooth_adapter_unavailable"
    return "audio_path_diagnostic"
