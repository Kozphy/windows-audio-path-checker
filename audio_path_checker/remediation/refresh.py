"""Non-elevated refresh helpers for Windows audio endpoint inventory.

Invariant C: R1 must never mutate pairing, registry, adapter, or remove PnP.
Invariant D: command exit code ≠ recovery success — postconditions required.
"""

from __future__ import annotations

import subprocess
import sys
import time
from collections.abc import Callable
from typing import Any

from ..diagnostics_engine.classifier import classify_state
from ..models.states import PATH_MATURITY, AudioPathState

# Bounded settle schedule (ms). First sample is immediate.
DEFAULT_SETTLE_SCHEDULE_MS: tuple[int, ...] = (0, 500, 1000, 2000, 3000)

_MUTATION_FORBIDDEN = (
    "Disable-PnpDevice",
    "Enable-PnpDevice",
    "Remove-PnpDevice",
    "pnputil",
    "Restart-Service",
    "Stop-Service",
)


def _query_inventory(*, timeout: int) -> dict[str, Any]:
    """Re-query MEDIA and AudioEndpoint PnP classes (read-only)."""
    result: dict[str, Any] = {
        "attempted": False,
        "command_succeeded": False,
        "classes": ["MEDIA", "AudioEndpoint", "Bluetooth"],
    }
    if sys.platform != "win32":
        result["detail"] = "unsupported_platform"
        return result

    script = (
        "$ErrorActionPreference = 'Stop'\n"
        "if (-not (Get-Command Get-PnpDevice -ErrorAction SilentlyContinue)) {\n"
        "  throw 'Get-PnpDevice is unavailable'\n"
        "}\n"
        "$classes = @('MEDIA', 'AudioEndpoint', 'Bluetooth')\n"
        "foreach ($class in $classes) {\n"
        "  $null = @(Get-PnpDevice -Class $class -ErrorAction SilentlyContinue)\n"
        "}\n"
    )
    for banned in ("Disable-PnpDevice", "Enable-PnpDevice", "Remove-PnpDevice", "pnputil", "Restart-Service"):
        if banned.casefold() in script.casefold():
            raise RuntimeError(f"R1 inventory script must not contain {banned}")

    result["attempted"] = True
    try:
        completed = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001
        result.update(
            {
                "detail": "inventory_query_failed",
                "error_type": type(exc).__name__,
                "error": str(exc)[:500],
            }
        )
        return result

    succeeded = completed.returncode == 0
    result["command_succeeded"] = succeeded
    result["returncode"] = completed.returncode
    result["detail"] = "inventory_queried" if succeeded else "inventory_query_failed"
    if not succeeded and completed.stderr:
        result["error"] = completed.stderr.strip()[:500]
    return result


def refresh_audio_endpoint_inventory(
    *,
    timeout: int = 20,
    settle_seconds: float | None = None,
    schedule_ms: tuple[int, ...] | list[int] | None = None,
    collect_fn: Callable[[], dict[str, Any]] | None = None,
    sleep: Callable[[float], None] | None = None,
) -> dict[str, Any]:
    """R1 bounded MEDIA/AudioEndpoint inventory refresh with optional settle loop.

    Args:
        timeout: PowerShell query timeout seconds.
        settle_seconds: Legacy single sleep after one query (used when
            ``collect_fn`` is absent and ``schedule_ms`` is None).
        schedule_ms: Backoff schedule for closed-loop sampling. Default
            ``(0, 500, 1000, 2000, 3000)`` when ``collect_fn`` is provided.
        collect_fn: Callable returning fresh evidence after each wait.
        sleep: Injectable sleep for tests; defaults to ``time.sleep``.
    """
    sleep_fn = sleep or time.sleep
    result: dict[str, Any] = {
        "action": "refresh_audio_endpoint_inventory",
        "risk": "R1",
        "attempted": False,
        "command_succeeded": False,
        "classes": ["MEDIA", "AudioEndpoint", "Bluetooth"],
        "attempts": [],
        "recovered": False,
        "progress": False,
        "postcondition_met": False,
        "escalation_recommended": None,
    }

    query = _query_inventory(timeout=timeout)
    result.update({k: query[k] for k in ("attempted", "command_succeeded", "detail") if k in query})
    for key in ("returncode", "error", "error_type"):
        if key in query:
            result[key] = query[key]

    if collect_fn is None:
        # Legacy one-shot path used by older callers/tests.
        delay = 2.0 if settle_seconds is None else float(settle_seconds)
        if result.get("command_succeeded") and delay > 0:
            sleep_fn(delay)
        return result

    schedule = tuple(schedule_ms or DEFAULT_SETTLE_SCHEDULE_MS)
    started_maturity = None
    best_maturity = -1
    last_state = None
    recovered = False

    cumulative = 0
    for index, wait_ms in enumerate(schedule):
        delta = wait_ms - cumulative
        if delta > 0:
            sleep_fn(delta / 1000.0)
        cumulative = wait_ms

        # Re-touch inventory between samples (still read-only).
        if index > 0:
            touch = _query_inventory(timeout=timeout)
            result["command_succeeded"] = result.get("command_succeeded") or bool(
                touch.get("command_succeeded")
            )

        evidence = collect_fn()
        classification = classify_state(
            evidence, settling=True, elapsed_ms=wait_ms
        )
        state = str(classification.get("state") or "")
        maturity = int(
            classification.get("maturity")
            or PATH_MATURITY.get(state, 0)
        )
        if started_maturity is None:
            started_maturity = maturity
        best_maturity = max(best_maturity, maturity)
        last_state = state

        flags = (classification.get("evidence_graph") or {}).get("flags") or {}
        attempt = {
            "attempt": index + 1,
            "elapsed_ms": wait_ms,
            "state": state,
            "maturity": maturity,
            "media": bool(flags.get("media_present")),
            "endpoint": bool(flags.get("endpoint_present")),
            "connected": bool(flags.get("connected")),
            "a2dp": bool(flags.get("a2dp_present")),
        }
        result["attempts"].append(attempt)

        # Stop early: recovered.
        if state == AudioPathState.AUDIO_PATH_HEALTHY.value or (
            flags.get("connected")
            and flags.get("media_present")
            and flags.get("endpoint_present")
        ):
            recovered = True
            result["postcondition_met"] = True
            break

        # Stop early: genuinely disconnected — further settle cannot help.
        if (
            not flags.get("connected")
            and not flags.get("inventory_present")
            and state
            in {
                AudioPathState.PAIRED_NOT_CONNECTED.value,
                AudioPathState.DEVICE_NOT_PAIRED.value,
                AudioPathState.RADIO_UNAVAILABLE.value,
            }
        ):
            result["escalation_recommended"] = "connect_headset_and_recheck"
            break

        # Hard failure after settle budget for connected stack.
        if index == len(schedule) - 1 and flags.get("connected"):
            result["escalation_recommended"] = "restart_bluetooth_audio_services"

    result["recovered"] = recovered
    result["progress"] = bool(
        started_maturity is not None and best_maturity > started_maturity
    )
    result["final_state"] = last_state
    result["maturity_before"] = started_maturity
    result["maturity_after"] = best_maturity
    if recovered:
        result["detail"] = "postcondition_met"
    elif result.get("progress"):
        result["detail"] = "partial_progress"
    elif result.get("escalation_recommended") == "connect_headset_and_recheck":
        result["detail"] = "device_disconnected_during_settle"
    return result
