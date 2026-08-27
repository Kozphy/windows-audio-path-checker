"""Risk-aware remediation planning; never silent wipe / never R5 for wrong default.

Maps diagnosed ``AudioPathState`` and primary root cause to ordered action
lists tagged R0–R5. Branch order is **usefulness-first** (recommended action
is ``actions[0]``), not a strict ascending risk sort in every branch.

Signal→action mapping examples:

* ``ENDPOINT_NOT_DEFAULT`` / ``wrong_default_output`` → R1 set default first,
  then R0 open Sound settings (never R5 re-pair)
* ``RADIO_UNAVAILABLE`` → R3 enable adapter
* ``DEVICE_NOT_PAIRED`` → R0 open Settings, R1 WinRT auto-pair
* ``AUDIO_SERVICE_FAILURE`` → R2 restart Audiosrv / Endpoint Builder
* A paired but genuinely disconnected device → R0 reconnect and recheck
* Connected stack breakages (no endpoint / no A2DP) → R1 refresh → R2
  services → R3 re-enumerate → R4 radio bounce → R5 scoped re-pair

Wrong default output must never trigger pairing reset.
"""

from __future__ import annotations

from typing import Any

from ..models.states import AudioPathState

# R0 observation … R5 remove/re-pair (lowest index = safest action).
RISK_ORDER = ("R0", "R1", "R2", "R3", "R4", "R5")


def plan_remediation(
    *,
    classification: dict[str, Any],
    hypotheses: list[dict[str, Any]],
    evidence: dict[str, Any],
    mode: str = "diagnose",
    attempted_actions: set[str] | list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Build a risk-gated remediation plan from diagnosis output.

    Args:
        classification: Output of :func:`~..diagnostics_engine.classifier.classify_state`.
        hypotheses: Ranked causes from
            :func:`~..diagnostics_engine.root_cause.rank_hypotheses`.
        evidence: Normalized evidence (used for device name scoping).
        mode: Execution cap — ``diagnose``/``dry-run`` (R0), ``repair`` (R3),
            or ``aggressive-repair`` (R5).
        attempted_actions: Successfully attempted actions that should not be
            recommended again during the same recovery flow.

    Returns:
        Plan dict with:

        * ``recommended`` — first action in the branch list (usefulness order)
        * ``actions`` — subset of planned actions allowed by ``max_risk``
        * ``blocked_actions`` — planned actions above ``max_risk``
        * ``executable`` — whether **the recommended action itself** is within
          ``max_risk`` (can be False while ``actions`` still holds safer items)

    Notes:
        Mode gates execution eligibility, not recommendation. Diagnose mode
        still recommends the most useful action so operators see what would run
        under ``--repair`` / ``--aggressive-repair``.
    """
    state = str(classification.get("state") or AudioPathState.UNKNOWN.value)
    top = hypotheses[0] if hypotheses else {"cause": "unknown", "confidence": 0.0}
    cause = str(top.get("cause") or "unknown")
    max_risk = {
        "diagnose": "R0",
        "dry-run": "R0",
        "repair": "R3",
        "aggressive-repair": "R5",
    }.get(mode, "R0")

    attempted = set(attempted_actions or ())
    actions = [
        action
        for action in _actions_for(state, cause, evidence)
        if action["action"] not in attempted
    ]
    # Recommend the safest useful action even in diagnose/dry-run.
    # Mode only gates what may be *executed*.
    executable = [a for a in actions if _risk_rank(a["risk"]) <= _risk_rank(max_risk)]
    blocked = [a for a in actions if _risk_rank(a["risk"]) > _risk_rank(max_risk)]
    recommended = actions[0] if actions else None

    return {
        "mode": mode,
        "max_risk": max_risk,
        "state": state,
        "primary_cause": cause,
        "attempted_actions": sorted(attempted),
        "recommended": recommended,
        "actions": executable,
        "blocked_actions": blocked,
        "executable": recommended is not None
        and _risk_rank(str(recommended.get("risk"))) <= _risk_rank(max_risk),
        "notes": _notes(state, cause, mode),
    }


def _risk_rank(risk: str) -> int:
    """Return numeric index for risk label (lower = safer).

    Args:
        risk: Risk level string (``R0``–``R5``).

    Returns:
        Index in :data:`RISK_ORDER`, or ``99`` for unknown labels.
    """
    try:
        return RISK_ORDER.index(risk)
    except ValueError:
        return 99


def _actions_for(
    state: str, cause: str, evidence: dict[str, Any]
) -> list[dict[str, Any]]:
    """Map state and cause signals to ordered remediation actions.

    Args:
        state: Classified :class:`~..models.states.AudioPathState` value.
        cause: Top hypothesis cause string.
        evidence: Evidence document (device name for scoping).

    Returns:
        Actions sorted lowest risk first. Each action includes ``action``,
        ``risk`` (R0–R5), ``reason``, ``requires_elevation``, ``scoped_device``,
        and ``verify`` (post-action checklist feature names).
    """
    device = (evidence.get("device") or {}).get("name") or "target headset"
    actions: list[dict[str, Any]] = []

    def add(
        action: str,
        risk: str,
        reason: str,
        *,
        elevates: bool = False,
        verifies: list[str] | None = None,
    ) -> None:
        actions.append(
            {
                "action": action,
                "risk": risk,
                "reason": reason,
                "requires_elevation": elevates,
                "scoped_device": device,
                "verify": verifies or [],
            }
        )

    if state == AudioPathState.AUDIO_PATH_HEALTHY.value:
        add("none", "R0", "Path already healthy — no remediation")
        return actions

    if state in {
        AudioPathState.PROFILE_ENUMERATION_PENDING.value,
        AudioPathState.ENDPOINT_ENUMERATION_PENDING.value,
    }:
        add(
            "refresh_audio_endpoint_inventory",
            "R1",
            "Bounded settle: re-query MEDIA / AudioEndpoint while enumeration completes",
            verifies=["media_node_present", "endpoint_present"],
        )
        add(
            "restart_bluetooth_audio_services",
            "R2",
            "Escalate only if settle exhausts without progress",
            elevates=True,
            verifies=["a2dp_present", "endpoint_present"],
        )
        return actions

    if state == AudioPathState.ENDPOINT_NOT_DEFAULT.value or cause == "wrong_default_output":
        add(
            "set_default_playback_to_headset",
            "R1",
            "Endpoint exists; wrong default output — do not reset Bluetooth",
            verifies=["is_default_playback", "endpoint_active"],
        )
        add("open_sound_settings", "R0", "User can set default output manually")
        return actions

    if state == AudioPathState.RADIO_UNAVAILABLE.value:
        add(
            "enable_bluetooth_adapter",
            "R3",
            "Adapter disabled / Error — enable radio",
            elevates=True,
            verifies=["adapter_enabled"],
        )
        return actions

    if state == AudioPathState.DEVICE_NOT_PAIRED.value:
        add("open_bluetooth_settings", "R0", "User must put headset in pairing mode")
        add(
            "auto_pair_if_winrt_available",
            "R1",
            "Attempt WinRT pair only when capability probe succeeds",
            verifies=["device_paired", "endpoint_present"],
        )
        return actions

    if state == AudioPathState.AUDIO_SERVICE_FAILURE.value:
        add(
            "restart_audio_services",
            "R2",
            "Audiosrv / Endpoint Builder unhealthy",
            elevates=True,
            verifies=["audio_services_healthy", "endpoint_present"],
        )
        return actions

    if state == AudioPathState.PAIRED_NOT_CONNECTED.value:
        add(
            "connect_headset_and_recheck",
            "R0",
            "Device is paired but no live Bluetooth/A2DP connection is observed; "
            "power on / connect the headset before repairing endpoint inventory",
            verifies=["device_connected"],
        )
        return actions

    if state == AudioPathState.STALE_PNP_INVENTORY.value or cause == "stale_pnp_state":
        add(
            "refresh_audio_endpoint_inventory",
            "R1",
            "Identity-matched inventory exists while connected=false — re-query to "
            "confirm ghosts vs reconnect race",
            verifies=["device_connected", "endpoint_present"],
        )
        add(
            "connect_headset_and_recheck",
            "R0",
            "If refresh still shows disconnected, reconnect the headset",
            verifies=["device_connected"],
        )
        add(
            "reenumerate_headset_audio_stack",
            "R3",
            "Scoped PnP refresh for the target headset address only",
            elevates=True,
            verifies=["media_node_present", "endpoint_present"],
        )
        add(
            "clear_pairing_cache_and_repair",
            "R5",
            "Clear BTHPORT cache for scoped address only, then re-pair",
            elevates=True,
            verifies=["device_paired", "endpoint_present", "endpoint_active"],
        )
        return actions

    if state in {
        AudioPathState.MEDIA_NO_ENDPOINT.value,
        AudioPathState.CONNECTED_NO_A2DP.value,
        AudioPathState.A2DP_NO_MEDIA_NODE.value,
        AudioPathState.ENDPOINT_DISABLED.value,
    }:
        add(
            "refresh_audio_endpoint_inventory",
            "R1",
            "Low-risk bounded re-query of MEDIA / AudioEndpoint",
            verifies=["endpoint_present"],
        )
        add(
            "restart_bluetooth_audio_services",
            "R2",
            "Restart BthAvctpSvc / BTAGService only (not full adapter reset)",
            elevates=True,
            verifies=["a2dp_present", "endpoint_present"],
        )
        add(
            "reenumerate_headset_audio_stack",
            "R3",
            "Scoped PnP refresh for the target headset address only",
            elevates=True,
            verifies=["media_node_present", "endpoint_present"],
        )
        add(
            "adapter_radio_bounce",
            "R4",
            "Disable/enable Bluetooth adapter",
            elevates=True,
            verifies=["adapter_enabled", "device_connected"],
        )
        add(
            "clear_pairing_cache_and_repair",
            "R5",
            "Clear BTHPORT cache for scoped address only, then re-pair",
            elevates=True,
            verifies=["device_paired", "endpoint_present", "endpoint_active"],
        )
        return actions

    if state == AudioPathState.INSUFFICIENT_EVIDENCE.value:
        add("collect_additional_evidence", "R0", "Evidence incomplete — gather more signals")
        return actions

    add("collect_additional_evidence", "R0", "State unknown — gather more signals")
    return actions


def _notes(state: str, cause: str, mode: str) -> list[str]:
    """Build human-readable plan footnotes.

    Args:
        state: Classified state value.
        cause: Primary root-cause label.
        mode: Remediation mode string.

    Returns:
        List of explanatory note strings for the plan output.
    """
    notes = [
        "Bluetooth Connected is not equivalent to Audio Working.",
        f"Diagnosed state={state}; primary_cause={cause}; mode={mode}.",
    ]
    if cause == "wrong_default_output":
        notes.append("Pairing reset is blocked for wrong-default-output cases.")
    if mode in {"diagnose", "dry-run"}:
        notes.append("No disruptive actions will execute in this mode.")
    return notes
