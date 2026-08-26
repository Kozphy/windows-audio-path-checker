from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from typing import Any, Callable


def _finding_codes(snapshot: dict[str, Any]) -> set[str]:
    return {
        str(item.get("code"))
        for item in snapshot.get("findings") or []
        if item.get("code")
    }


def compact_state(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Extract stable, decision-useful state from a potentially noisy snapshot."""
    core = snapshot.get("core_audio") or {}
    endpoint = core.get("default_endpoint") or {}
    portaudio = snapshot.get("portaudio") or {}
    bluetooth = snapshot.get("bluetooth") or {}
    sessions = []
    for item in core.get("sessions") or []:
        if not item.get("is_browser"):
            continue
        sessions.append(
            {
                "process": item.get("process"),
                "pid": item.get("pid"),
                "muted": bool(item.get("muted")),
                "volume": item.get("volume"),
                "state": item.get("state"),
                "output_device": item.get("output_device"),
            }
        )
    sessions.sort(key=lambda item: (str(item.get("process")), int(item.get("pid") or 0)))
    return {
        "endpoint": endpoint.get("name"),
        "endpoint_id": endpoint.get("id"),
        "master_muted": core.get("master_muted"),
        "master_volume": core.get("master_volume"),
        "app_default_output": portaudio.get("default_output_name"),
        "browser_sessions": sessions,
        "finding_codes": sorted(_finding_codes(snapshot)),
        "bluetooth_endpoint_present": bluetooth.get("default_endpoint_present"),
    }


def state_fingerprint(snapshot: dict[str, Any]) -> str:
    payload = json.dumps(compact_state(snapshot), sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def diff_states(previous: dict[str, Any], current: dict[str, Any]) -> list[dict[str, Any]]:
    """Return semantic transitions between two snapshots."""
    before = compact_state(previous)
    after = compact_state(current)
    transitions: list[dict[str, Any]] = []

    for field in (
        "endpoint",
        "endpoint_id",
        "master_muted",
        "master_volume",
        "app_default_output",
        "bluetooth_endpoint_present",
    ):
        if before.get(field) != after.get(field):
            transitions.append({"type": "state-change", "field": field, "before": before.get(field), "after": after.get(field)})

    before_codes = set(before["finding_codes"])
    after_codes = set(after["finding_codes"])
    for code in sorted(after_codes - before_codes):
        transitions.append({"type": "finding-opened", "code": code})
    for code in sorted(before_codes - after_codes):
        transitions.append({"type": "finding-resolved", "code": code})

    if before["browser_sessions"] != after["browser_sessions"]:
        transitions.append(
            {
                "type": "browser-session-change",
                "before": before["browser_sessions"],
                "after": after["browser_sessions"],
            }
        )
    return transitions


def record_timeline(
    scanner: Callable[[], dict[str, Any]],
    *,
    duration_seconds: float,
    interval_seconds: float = 5.0,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Sample audio state and retain only meaningful transitions plus snapshots."""
    if duration_seconds <= 0:
        raise ValueError("duration_seconds must be > 0")
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be > 0")

    started = clock()
    samples: list[dict[str, Any]] = []
    transitions: list[dict[str, Any]] = []
    previous: dict[str, Any] | None = None

    while True:
        snapshot = scanner()
        observed_at = datetime.now(timezone.utc).isoformat()
        sample = {
            "observed_at": observed_at,
            "fingerprint": state_fingerprint(snapshot),
            "state": compact_state(snapshot),
            "snapshot": snapshot,
        }
        samples.append(sample)

        if previous is not None:
            for event in diff_states(previous, snapshot):
                transitions.append({"observed_at": observed_at, **event})
        previous = snapshot

        elapsed = clock() - started
        if elapsed >= duration_seconds:
            break
        sleep(min(interval_seconds, max(0.0, duration_seconds - elapsed)))

    return {
        "schema_version": 1,
        "duration_seconds": round(clock() - started, 3),
        "interval_seconds": interval_seconds,
        "sample_count": len(samples),
        "transition_count": len(transitions),
        "samples": samples,
        "transitions": transitions,
        "metrics": timeline_metrics(samples, transitions),
    }


def timeline_metrics(samples: list[dict[str, Any]], transitions: list[dict[str, Any]]) -> dict[str, Any]:
    """Produce small SRE-style metrics that remain meaningful for local diagnosis."""
    if not samples:
        return {"sample_count": 0, "unique_states": 0, "state_change_rate": 0.0, "critical_sample_ratio": 0.0}

    unique_states = len({str(item.get("fingerprint")) for item in samples})
    critical_samples = 0
    for item in samples:
        findings = ((item.get("snapshot") or {}).get("findings") or [])
        if any(finding.get("severity") == "critical" for finding in findings):
            critical_samples += 1

    opportunities = max(1, len(samples) - 1)
    return {
        "sample_count": len(samples),
        "unique_states": unique_states,
        "transition_count": len(transitions),
        "state_change_rate": round(len(transitions) / opportunities, 4),
        "critical_sample_ratio": round(critical_samples / len(samples), 4),
    }
