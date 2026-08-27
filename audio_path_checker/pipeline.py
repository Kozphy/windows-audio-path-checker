"""End-to-end audio-path diagnosis pipeline.

Data flow (input → transform → output)
--------------------------------------

1. **Input** — ``device_name``, optional pre-built ``snapshot`` / ``evidence``,
   ``mode`` (``diagnose`` | ``repair`` | ``aggressive-repair``), and flags
   controlling artifact persistence and execution.

2. **Evidence collection** — When ``evidence`` is absent,
   :func:`~audio_path_checker.collectors.evidence.collect_evidence` gathers
   Bluetooth pairing, A2DP/media-node, endpoint, service, and WinRT capability
   signals for the target device. Pre-supplied evidence may be augmented with a
   WinRT capability probe when missing.

3. **Diagnosis** — A :class:`~audio_path_checker.providers.diagnosis.DiagnosisProvider`
   maps evidence to a **classification** (audio-path state), **invariants**
   (expected vs observed), and ranked **hypotheses** (likely root causes).

4. **Remediation planning** — :func:`~audio_path_checker.remediation.planner.plan_remediation`
   selects a **recommended** action (or observation-only) from the
   classification, hypotheses, evidence, and mode. Mode caps which planned
   actions are marked executable; it does not itself run them.

5. **Optional reobserve experiment** — When ``execute=True`` *and* the
   recommended action is specifically ``refresh_audio_endpoint_inventory``,
   the pipeline waits briefly and re-collects evidence. It does **not** execute
   service restarts, adapter enablement, or pairing repair from this path.

6. **Verification** — :func:`~audio_path_checker.remediation.verification.verify_recovery`
   compares before/after evidence and whether that reobserve experiment ran.

7. **Output** — Human-readable ``report_text``, structured ``diagnosis`` /
   ``plan`` / ``verification``, a machine-learning-oriented ``dataset_record``,
   ``summary`` metadata, and optional on-disk session artifacts under
   ``session_dir``.
"""

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
    """Render a console-friendly diagnostic report for a single device.

    Summarizes pass/fail checks across the Bluetooth and audio stack,
    the top hypothesis, recommended remediation, capability gaps, and any
    invariant violations.

    Args:
        device_name: Human-readable label for the headset or endpoint under test.
        evidence: Collected evidence bundle (Bluetooth, audio, device, services).
        diagnosis: Provider output containing ``classification``, ``hypotheses``,
            and ``invariants``.
        plan: Remediation plan containing an optional ``recommended`` action.

    Returns:
        Multi-line plain-text report suitable for CLI or log output.
    """
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
    """Run the full evidence → diagnosis → plan → verify pipeline.

    Orchestrates collection (or reuse) of device evidence, provider-based
    classification, remediation planning, optional reobserve experiment,
    recovery verification, report generation, and optional session artifacts.

    Args:
        device_name: Target Bluetooth headset or audio device name.
        mode: Cap passed to the remediation planner (``diagnose`` / ``repair`` /
            ``aggressive-repair``). Affects which planned actions are marked
            executable; does not by itself run service/adapter/pairing repairs.
        snapshot: Optional pre-collected GUI/CLI snapshot to seed evidence
            collection.
        evidence: Pre-built evidence dict; when provided, collection is skipped
            except for a WinRT capability probe if ``capabilities`` is missing.
        provider: Diagnosis backend; defaults to the configured provider chain.
        artifacts_root: Parent directory for session folders; uses the default
            artifacts location when ``None``.
        write_artifacts: When true, persist evidence, diagnosis, actions, and
            dataset records under a new session directory.
        execute: When true, and only if the recommended action is
            ``refresh_audio_endpoint_inventory``, wait briefly and re-collect
            evidence. No PnP/service/pairing mutations are performed here.

    Returns:
        Result bundle with keys:

        * ``session_dir`` — artifact folder path or ``None``
        * ``evidence`` / ``evidence_after`` — before and after evidence
        * ``diagnosis`` / ``plan`` — structured provider and planner output
        * ``verification`` — recovery verdict from the verifier
        * ``report_text`` — human-readable summary
        * ``dataset_record`` — flat record for ML / analytics export
        * ``summary`` — high-level metadata (classification, timing, mode)

    Notes:
        Destructive or elevated Bluetooth repairs live in
        :mod:`audio_path_checker.bluetooth` / CLI flags (``--add-bluetooth``,
        ``--repair-bluetooth``, ``--enable-bluetooth-adapter``), not in this
        pipeline execute path.
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
    """Map collected evidence to a coarse symptom label for dataset export.

    Args:
        evidence: Evidence bundle with ``device``, ``audio``, and ``bluetooth``
            sub-dicts.

    Returns:
        A stable symptom code such as ``bluetooth_connected_no_audio`` or
        ``device_not_paired``, or the generic ``audio_path_diagnostic``.
    """
    device = evidence.get("device") or {}
    audio = evidence.get("audio") or {}
    if device.get("connected") and not audio.get("endpoint_present"):
        return "bluetooth_connected_no_audio"
    if not device.get("paired"):
        return "device_not_paired"
    if not (evidence.get("bluetooth") or {}).get("adapter_enabled"):
        return "bluetooth_adapter_unavailable"
    return "audio_path_diagnostic"
