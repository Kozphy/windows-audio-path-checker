"""Post-action verification: command success ≠ problem resolved."""

from __future__ import annotations

from typing import Any

from ..diagnostics_engine.classifier import classify_state
from ..models.states import AudioPathState


def verify_recovery(
    *,
    evidence_after: dict[str, Any],
    repair_command_succeeded: bool,
    classification_after: dict[str, Any] | None = None,
) -> dict[str, Any]:
    classification = classification_after or classify_state(evidence_after)
    state = str(classification.get("state") or "")
    recovered = state == AudioPathState.AUDIO_PATH_HEALTHY.value
    audio = evidence_after.get("audio") or {}
    device = evidence_after.get("device") or {}
    services = evidence_after.get("services") or {}

    checklist = {
        "bluetooth_connected": bool(device.get("connected")),
        "a2dp_available": bool(audio.get("a2dp_present") or audio.get("media_node_present")),
        "media_node": bool(audio.get("media_node_present")),
        "audio_endpoint": bool(audio.get("endpoint_present")),
        "endpoint_active": bool(audio.get("endpoint_active")),
        "audio_services": str(services.get("Audiosrv", "")).casefold() == "running",
        "default_output": audio.get("is_default_playback"),
    }

    return {
        "repair_command_succeeded": bool(repair_command_succeeded),
        "system_recovered": recovered,
        "verified_state": state,
        "confidence": classification.get("confidence"),
        "checklist": checklist,
        "distinction": {
            "action_succeeded": bool(repair_command_succeeded),
            "problem_resolved": recovered,
        },
    }
