"""End-to-end audio-path diagnosis pipeline.

Observe → classify → plan → (optional R1 settle) → verify → audit artifacts.

Destructive Bluetooth repairs remain outside this path (``bluetooth`` / CLI).
"""

from __future__ import annotations

import json
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
from .remediation.refresh import refresh_audio_endpoint_inventory
from .remediation.verification import verify_recovery
from .session.artifacts import (
    append_action,
    append_recovery,
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
    recovery: dict[str, Any] | None = None,
) -> str:
    """Render a console-friendly diagnostic report with PASS/FAIL/PENDING/N/A."""
    classification = diagnosis.get("classification") or {}
    hypotheses = diagnosis.get("hypotheses") or []
    top = hypotheses[0] if hypotheses else {}
    services = evidence.get("services") or {}
    state = str(classification.get("state") or "UNKNOWN")
    checks = classification.get("checks") or {}
    conf = classification.get("confidence_detail") or {}
    graph = classification.get("evidence_graph") or {}
    target = graph.get("target") or {}

    def status(key: str, fallback: str = "UNKNOWN") -> str:
        return str(checks.get(key) or fallback)

    lines = [
        f"{device_name} — Audio Path Diagnostic",
        "",
        f"{'Bluetooth Adapter':<22} {status('adapter')}",
        f"{'Device Identity':<22} {status('identity')}",
        f"{'Device Paired':<22} {status('paired')}",
        f"{'Device Connected':<22} {status('connected')}",
        f"{'A2DP Profile':<22} {status('a2dp')}",
        f"{'MEDIA Node':<22} {status('media')}",
        f"{'Audio Endpoint':<22} {status('endpoint')}",
        f"{'Windows Audio':<22} {status('windows_audio')}",
        f"{'Default Output':<22} {status('default_output')}",
        "",
        "Diagnosis",
        "---------",
        f"State: {state}",
        f"Likely cause: {top.get('cause', 'unknown')}",
        f"Confidence: {conf.get('confidence_label', 'n/a')} "
        f"({float(classification.get('confidence') or 0):.0%})",
        f"Failure category: {classification.get('failure_category', 'UNKNOWN')}",
        f"Path maturity: {classification.get('maturity', 0)}/6",
    ]
    if target.get("canonical_bluetooth_address"):
        lines.append(f"Target address: {target.get('canonical_bluetooth_address')}")

    supporting = conf.get("supporting_evidence") or classification.get("evidence_ids") or []
    if supporting:
        lines.extend(["", "Evidence", "--------"])
        for item in supporting[:12]:
            lines.append(f"- {item}")
    contradictions = conf.get("contradictions") or []
    if contradictions:
        lines.extend(["", "Contradictions", "--------------"])
        for item in contradictions:
            lines.append(f"- {item}")

    lines.extend(["", "Recommended action", "------------------"])
    recommended = plan.get("recommended")
    if recommended:
        lines.append(str(recommended.get("action")))
        lines.append(str(recommended.get("reason")))
        lines.append(f"Risk: {recommended.get('risk')}")
    else:
        lines.append("None (observation only or path healthy).")

    if recovery and recovery.get("attempts"):
        lines.extend(["", "Settle attempts", "---------------"])
        for attempt in recovery["attempts"]:
            lines.append(
                f"{attempt.get('attempt')}/{len(recovery['attempts'])} "
                f"elapsed={attempt.get('elapsed_ms')}ms "
                f"state={attempt.get('state')} "
                f"MEDIA={'present' if attempt.get('media') else 'missing'} "
                f"AudioEndpoint={'present' if attempt.get('endpoint') else 'missing'}"
            )
        result_label = (
            "RECOVERED"
            if recovery.get("recovered")
            else "PARTIAL_PROGRESS"
            if recovery.get("progress")
            else "NO_PROGRESS"
        )
        lines.extend(["", f"Result: {result_label}"])
        if recovery.get("escalation_recommended"):
            lines.append(f"Next escalation: {recovery.get('escalation_recommended')}")

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
    # Silence unused services unless windows_audio unknown
    _ = services
    return "\n".join(lines)


def replay_session(
    session_dir: str | Path,
    *,
    write_artifacts: bool = False,
) -> dict[str, Any]:
    """Re-run classification against captured evidence without touching hardware."""
    path = Path(session_dir)
    evidence_path = path / "evidence-before.json"
    if not evidence_path.is_file():
        raise FileNotFoundError(f"No evidence-before.json in {path}")
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    old_diagnosis = None
    diagnosis_path = path / "diagnosis.json"
    if diagnosis_path.is_file():
        old_diagnosis = json.loads(diagnosis_path.read_text(encoding="utf-8"))

    result = run_audio_path_diagnosis(
        device_name=str(
            (evidence.get("device") or {}).get("name")
            or (evidence.get("environment") or {}).get("device_filter")
            or "device"
        ),
        mode="diagnose",
        evidence=evidence,
        write_artifacts=write_artifacts,
        execute=False,
    )
    new_state = str(
        ((result.get("diagnosis") or {}).get("classification") or {}).get("state")
    )
    old_state = str(
        ((old_diagnosis or {}).get("classification") or {}).get("state") or ""
    )
    old_cause = str(
        (((old_diagnosis or {}).get("hypotheses") or [{}])[0]).get("cause") or ""
    )
    new_cause = str(
        (((result.get("diagnosis") or {}).get("hypotheses") or [{}])[0]).get("cause")
        or ""
    )
    result["shadow_comparison"] = {
        "session": str(path),
        "old_state": old_state,
        "new_state": new_state,
        "old_cause": old_cause,
        "new_cause": new_cause,
        "changed": old_state != new_state or old_cause != new_cause,
    }
    return result


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
    """Run evidence → diagnosis → plan → optional R1 settle → verify."""
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
    diagnosis_before = diagnosis
    plan = plan_remediation(
        classification=diagnosis["classification"],
        hypotheses=diagnosis["hypotheses"],
        evidence=evidence,
        mode=mode,
    )

    actions_log: list[dict[str, Any]] = []
    recovery_meta: dict[str, Any] | None = None
    evidence_after = None
    verification = None
    repair_command_succeeded = False

    recommended = plan.get("recommended") or {}
    if (
        execute
        and mode in {"repair", "aggressive-repair"}
        and recommended.get("action") == "refresh_audio_endpoint_inventory"
    ):
        state_before = str(
            (diagnosis.get("classification") or {}).get("state")
            or AudioPathState.UNKNOWN.value
        )
        maturity_before = int(
            (diagnosis.get("classification") or {}).get("maturity") or 0
        )

        def _collect() -> dict[str, Any]:
            return collect_evidence(
                device_name=device_name,
                snapshot=snapshot,
                include_winrt_probe=False,
            )

        refresh_result = refresh_audio_endpoint_inventory(collect_fn=_collect)
        repair_command_succeeded = bool(refresh_result.get("command_succeeded"))
        recovery_meta = refresh_result
        evidence_after = _collect()
        # Final classification outside settle window (hard states if still broken).
        diagnosis = provider.diagnose(evidence_after)
        state_after = str(
            (diagnosis.get("classification") or {}).get("state")
            or AudioPathState.UNKNOWN.value
        )
        maturity_after = int(
            (diagnosis.get("classification") or {}).get("maturity") or 0
        )
        attempted_actions = (
            {"refresh_audio_endpoint_inventory"}
            if repair_command_succeeded
            else set()
        )
        # If settle already recommends next step, prefer that after failed postcondition.
        plan = plan_remediation(
            classification=diagnosis["classification"],
            hypotheses=diagnosis["hypotheses"],
            evidence=evidence_after,
            mode=mode,
            attempted_actions=attempted_actions,
        )
        if (
            refresh_result.get("escalation_recommended")
            and not refresh_result.get("recovered")
            and not refresh_result.get("progress")
        ):
            # Keep planner output but annotate.
            plan = dict(plan)
            plan["settle_escalation"] = refresh_result.get("escalation_recommended")

        postcondition = bool(
            refresh_result.get("postcondition_met")
            or state_after == AudioPathState.AUDIO_PATH_HEALTHY.value
        )
        progress = bool(
            refresh_result.get("progress") or maturity_after > maturity_before
        )
        exp = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": "refresh_audio_endpoint_inventory",
            "experiment": "refresh_audio_endpoint_inventory",
            "reason": recommended.get("reason"),
            "risk": "R1",
            "before": state_before,
            "after": state_after,
            "maturity_before": maturity_before,
            "maturity_after": maturity_after,
            "command_succeeded": repair_command_succeeded,
            "postcondition_met": postcondition,
            "progress": progress,
            "attempts": refresh_result.get("attempts") or [],
            "result": (
                "system_recovered"
                if postcondition
                else "partial_progress"
                if progress
                else "inventory_refreshed_no_recovery"
                if repair_command_succeeded
                else "inventory_refresh_failed"
            ),
            "detail": refresh_result.get("detail"),
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }
        if refresh_result.get("error"):
            exp["error"] = refresh_result["error"]
        if refresh_result.get("escalation_recommended"):
            exp["escalation_recommended"] = refresh_result["escalation_recommended"]
        actions_log.append(exp)
        verification = verify_recovery(
            evidence_after=evidence_after,
            repair_command_succeeded=postcondition,
            classification_after=diagnosis.get("classification"),
        )
        # Distinguish: command OK vs problem resolved.
        verification = dict(verification)
        verification["repair_command_succeeded"] = repair_command_succeeded
        verification["postcondition_met"] = postcondition
        verification["progress"] = progress

    if evidence_after is None:
        evidence_after = evidence
        verification = verify_recovery(
            evidence_after=evidence_after,
            repair_command_succeeded=repair_command_succeeded,
            classification_after=diagnosis.get("classification"),
        )

    report_text = format_diagnostic_report(
        device_name=device_name,
        evidence=evidence_after,
        diagnosis=diagnosis,
        plan=plan,
        recovery=recovery_meta,
    )

    case_id = session_dir.name if session_dir else str(uuid.uuid4())
    top = (diagnosis_before.get("hypotheses") or [{}])[0]
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

    # Enrich diagnosis artifact (schema v2) without dropping legacy keys.
    diagnosis_out = {
        **diagnosis,
        "schema_version": 2,
        "plan": plan,
        "device": (diagnosis.get("classification") or {})
        .get("evidence_graph", {})
        .get("target"),
        "recommended_action": (plan.get("recommended") or {}).get("action"),
        "risk_level": (plan.get("recommended") or {}).get("risk"),
        "recovery_attempts": (recovery_meta or {}).get("attempts") or [],
    }

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
            diagnosis=diagnosis_out,
            evidence_after=evidence_after,
            summary=summary,
            dataset_record=dataset,
        )
        for record in actions_log:
            append_action(session_dir, record)
            append_recovery(session_dir, record)
    return {
        "session_dir": str(session_dir) if session_dir else None,
        "evidence": evidence,
        "evidence_after": evidence_after,
        "diagnosis": diagnosis_out,
        "plan": plan,
        "verification": verification,
        "report_text": report_text,
        "dataset_record": dataset,
        "summary": summary,
        "recovery": recovery_meta,
    }


def _symptom(evidence: dict[str, Any]) -> str:
    """Map collected evidence to a coarse symptom label for dataset export."""
    device = evidence.get("device") or {}
    audio = evidence.get("audio") or {}
    if device.get("connected") and not audio.get("endpoint_present"):
        return "bluetooth_connected_no_audio"
    if not device.get("paired"):
        return "device_not_paired"
    if not (evidence.get("bluetooth") or {}).get("adapter_enabled"):
        return "bluetooth_adapter_unavailable"
    return "audio_path_diagnostic"
