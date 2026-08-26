"""Risk-aware remediation planning. Lowest risk first; never silent wipe."""

from __future__ import annotations

from typing import Any

from ..models.states import AudioPathState

# R0 observation … R5 remove/re-pair
RISK_ORDER = ("R0", "R1", "R2", "R3", "R4", "R5")


def plan_remediation(
    *,
    classification: dict[str, Any],
    hypotheses: list[dict[str, Any]],
    evidence: dict[str, Any],
    mode: str = "diagnose",
) -> dict[str, Any]:
    """
    Build a remediation plan.

    Modes:
      diagnose / dry-run — recommend only
      repair — allow up to R3 (non-destructive re-enumerate / enable)
      aggressive-repair — allow up to R5 (scoped pairing clear)
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

    actions = _actions_for(state, cause, evidence)
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
        "recommended": recommended,
        "actions": executable,
        "blocked_actions": blocked,
        "executable": recommended is not None
        and _risk_rank(str(recommended.get("risk"))) <= _risk_rank(max_risk),
        "notes": _notes(state, cause, mode),
    }


def _risk_rank(risk: str) -> int:
    try:
        return RISK_ORDER.index(risk)
    except ValueError:
        return 99


def _actions_for(
    state: str, cause: str, evidence: dict[str, Any]
) -> list[dict[str, Any]]:
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

    if state in {
        AudioPathState.MEDIA_NO_ENDPOINT.value,
        AudioPathState.CONNECTED_NO_A2DP.value,
        AudioPathState.A2DP_NO_MEDIA_NODE.value,
        AudioPathState.PAIRED_NOT_CONNECTED.value,
        AudioPathState.ENDPOINT_DISABLED.value,
    }:
        add(
            "refresh_audio_endpoint_inventory",
            "R1",
            "Low-risk re-query of MEDIA / AudioEndpoint after brief wait",
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

    add("collect_additional_evidence", "R0", "State unknown — gather more signals")
    return actions


def _notes(state: str, cause: str, mode: str) -> list[str]:
    notes = [
        "Bluetooth Connected is not equivalent to Audio Working.",
        f"Diagnosed state={state}; primary_cause={cause}; mode={mode}.",
    ]
    if cause == "wrong_default_output":
        notes.append("Pairing reset is blocked for wrong-default-output cases.")
    if mode in {"diagnose", "dry-run"}:
        notes.append("No disruptive actions will execute in this mode.")
    return notes
