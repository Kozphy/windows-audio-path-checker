"""Windows playback-path snapshot collection and rule-based findings.

Gathers read-only evidence from Windows audio services, PortAudio, Core Audio
(pycaw), and Bluetooth subsystems, then derives user-actionable findings for
common silent-headphone scenarios (muted master volume, per-app routing,
browser session silence, Bluetooth adapter/state desync).

Public entry points:

* :func:`collect_snapshot` — full system scan with embedded findings
* :func:`analyze_snapshot` — re-run finding rules on an existing snapshot
* :func:`play_test_tone` / :func:`stop_test_tone` — app-level playback test
* :func:`unmute_silent_browser_sessions` — targeted browser volume repair
"""

from __future__ import annotations

import json
import os
import platform
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .bluetooth import (
    collect_bluetooth,
    disabled_bluetooth_adapters,
    match_headset_for_endpoint,
)


BROWSER_PROCESSES = {
    "brave.exe",
    "chrome.exe",
    "firefox.exe",
    "msedge.exe",
    "opera.exe",
    "vivaldi.exe",
}

SEVERITY_ORDER = {"critical": 0, "warning": 1, "ok": 2, "info": 3}


def _error(source: str, exc: BaseException) -> dict[str, str]:
    """Build a structured collector error record."""
    return {
        "source": source,
        "type": type(exc).__name__,
        "message": str(exc),
    }


def _friendly_process_name(session: Any) -> str:
    """Resolve a display name for a Core Audio session object."""
    process = getattr(session, "Process", None)
    if process is not None:
        try:
            return str(process.name())
        except Exception:
            pass
    display_name = getattr(session, "DisplayName", None)
    if display_name:
        return str(display_name)
    pid = getattr(session, "ProcessId", 0)
    return "System Sounds" if pid == 0 else f"PID {pid}"


def _init_com() -> tuple[Any, Any]:
    """Initialize COM when available for pycaw/comtypes calls.

    Returns:
        Tuple of ``(CoInitialize, CoUninitialize)`` callables, or
        ``(None, None)`` when COM is unavailable.
    """
    try:
        from comtypes import CoInitialize, CoUninitialize

        CoInitialize()
        return CoInitialize, CoUninitialize
    except Exception:
        return None, None


def _collect_audio_services() -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Query Windows Audio and Audio Endpoint Builder service status.

    Returns:
        Tuple of ``(services, errors)`` where each service dict includes
        ``name``, ``friendly_name``, ``status``, and ``start_type``.
    """
    services: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    if sys.platform != "win32":
        return services, errors

    try:
        import psutil
    except Exception as exc:
        return services, [_error("Windows services", exc)]

    for service_name, friendly_name in (
        ("Audiosrv", "Windows Audio"),
        ("AudioEndpointBuilder", "Windows Audio Endpoint Builder"),
    ):
        try:
            service = psutil.win_service_get(service_name)
            data = service.as_dict()
            services.append(
                {
                    "name": service_name,
                    "friendly_name": friendly_name,
                    "status": data.get("status", "unknown"),
                    "start_type": data.get("start_type", "unknown"),
                }
            )
        except Exception as exc:
            errors.append(_error(f"Service {service_name}", exc))
    return services, errors


def _collect_portaudio() -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Enumerate app-visible playback devices via sounddevice (PortAudio).

    Returns:
        Tuple of ``(result, errors)`` where ``result`` contains default output
        index/name, host APIs, and filtered output device list.
    """
    result: dict[str, Any] = {
        "default_output_index": None,
        "default_output_name": None,
        "output_devices": [],
        "host_apis": [],
    }
    errors: list[dict[str, str]] = []

    try:
        import sounddevice as sd

        host_apis = list(sd.query_hostapis())
        result["host_apis"] = [
            {
                "index": index,
                "name": str(api.get("name", "")),
                "default_output_device": int(api.get("default_output_device", -1)),
            }
            for index, api in enumerate(host_apis)
        ]

        default_pair = sd.default.device
        try:
            default_output_index = int(default_pair[1])
        except (TypeError, ValueError, IndexError):
            default_output_index = None
        if default_output_index is not None and default_output_index < 0:
            default_output_index = None
        result["default_output_index"] = default_output_index

        for index, device in enumerate(sd.query_devices()):
            max_output_channels = int(device.get("max_output_channels", 0))
            if max_output_channels <= 0:
                continue
            host_index = int(device.get("hostapi", -1))
            host_name = (
                str(host_apis[host_index].get("name", "Unknown"))
                if 0 <= host_index < len(host_apis)
                else "Unknown"
            )
            item = {
                "index": index,
                "name": str(device.get("name", f"Device {index}")),
                "host_api": host_name,
                "max_output_channels": max_output_channels,
                "default_sample_rate": int(float(device.get("default_samplerate", 0))),
                "is_default": index == default_output_index,
            }
            result["output_devices"].append(item)
            if item["is_default"]:
                result["default_output_name"] = item["name"]
    except Exception as exc:
        errors.append(_error("Playback devices", exc))
    return result, errors


def _session_payload(
    session: Any, output_device: str | None, output_device_id: str | None
) -> dict[str, Any]:
    """Serialize one Core Audio session to a JSON-friendly dict."""
    simple_volume = session.SimpleAudioVolume
    process_name = _friendly_process_name(session)
    return {
        "process": process_name,
        "pid": int(getattr(session, "ProcessId", 0) or 0),
        "display_name": str(getattr(session, "DisplayName", "") or ""),
        "volume": round(float(simple_volume.GetMasterVolume()), 4),
        "muted": bool(simple_volume.GetMute()),
        "state": str(getattr(session, "State", "unknown")),
        "is_browser": process_name.casefold() in BROWSER_PROCESSES,
        "output_device": output_device,
        "output_device_id": output_device_id,
        "instance_id": str(getattr(session, "InstanceIdentifier", "") or ""),
    }


def _iter_device_sessions(device: Any) -> Iterable[Any]:
    """Return AudioSession wrappers for all sessions on a playback device.

    Returns an empty list when the device has no ``AudioSessionManager``.
    Despite the historical helper name, this builds a concrete list rather
    than a generator.
    """
    from pycaw.api.audiopolicy import IAudioSessionControl2
    from pycaw.utils import AudioSession

    manager = getattr(device, "AudioSessionManager", None)
    if manager is None:
        return []
    enumerator = manager.GetSessionEnumerator()
    count = int(enumerator.GetCount())
    sessions: list[Any] = []
    for index in range(count):
        control = enumerator.GetSession(index)
        if control is None:
            continue
        control2 = control.QueryInterface(IAudioSessionControl2)
        if control2 is not None:
            sessions.append(AudioSession(control2))
    return sessions


def _collect_core_audio() -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Collect default endpoint, master volume, and per-app session state.

    Walks all active render devices when possible; falls back to default-endpoint
    sessions on older Windows builds or partial COM availability.

    Returns:
        Tuple of ``(result, errors)`` with ``default_endpoint``, master
        volume/mute, and deduplicated ``sessions`` list.
    """
    result: dict[str, Any] = {
        "default_endpoint": None,
        "master_volume": None,
        "master_muted": None,
        "sessions": [],
    }
    errors: list[dict[str, str]] = []
    if sys.platform != "win32":
        return result, errors

    _, co_uninitialize = _init_com()
    try:
        from pycaw.constants import DEVICE_STATE, EDataFlow
        from pycaw.pycaw import AudioUtilities

        speakers = AudioUtilities.GetSpeakers()
        result["default_endpoint"] = {
            "name": str(getattr(speakers, "FriendlyName", "") or ""),
            "id": str(getattr(speakers, "id", "") or ""),
        }

        endpoint_volume = getattr(speakers, "EndpointVolume", None)
        if endpoint_volume is None:
            try:
                from ctypes import POINTER, cast

                from comtypes import CLSCTX_ALL
                from pycaw.pycaw import IAudioEndpointVolume

                interface = speakers.Activate(
                    IAudioEndpointVolume._iid_, CLSCTX_ALL, None
                )
                endpoint_volume = cast(interface, POINTER(IAudioEndpointVolume))
            except Exception as exc:
                errors.append(_error("Master endpoint volume", exc))

        if endpoint_volume is not None:
            try:
                result["master_volume"] = round(
                    float(endpoint_volume.GetMasterVolumeLevelScalar()), 4
                )
                result["master_muted"] = bool(endpoint_volume.GetMute())
            except Exception as exc:
                errors.append(_error("Master endpoint volume", exc))

        seen_instances: set[str] = set()
        playback_devices = AudioUtilities.GetAllDevices(
            data_flow=EDataFlow.eRender.value,
            device_state=DEVICE_STATE.ACTIVE.value,
        )
        for device in playback_devices:
            device_name = str(getattr(device, "FriendlyName", "") or "") or None
            device_id = str(getattr(device, "id", "") or "") or None
            try:
                device_sessions = _iter_device_sessions(device)
            except Exception as exc:
                errors.append(
                    _error(f"Sessions on {device_name or 'playback device'}", exc)
                )
                continue
            for session in device_sessions:
                try:
                    payload = _session_payload(session, device_name, device_id)
                    instance_id = payload.get("instance_id") or ""
                    dedupe_key = instance_id or (
                        f"{payload['pid']}:{payload['process']}:"
                        f"{device_id or device_name or ''}"
                    )
                    if dedupe_key in seen_instances:
                        continue
                    seen_instances.add(dedupe_key)
                    result["sessions"].append(payload)
                except Exception as exc:
                    errors.append(_error("Application audio session", exc))

        # Fallback: default-endpoint sessions only (older Windows / partial COM).
        if not result["sessions"]:
            for session in AudioUtilities.GetAllSessions():
                try:
                    default_name = (result["default_endpoint"] or {}).get("name")
                    default_id = (result["default_endpoint"] or {}).get("id")
                    result["sessions"].append(
                        _session_payload(session, default_name, default_id)
                    )
                except Exception as exc:
                    errors.append(_error("Application audio session", exc))
    except Exception as exc:
        errors.append(_error("Windows Core Audio", exc))
    finally:
        if co_uninitialize is not None:
            try:
                co_uninitialize()
            except Exception:
                pass
    return result, errors


def collect_snapshot() -> dict[str, Any]:
    """Collect a read-only snapshot of the Windows playback path.

    Aggregates service health, PortAudio devices, Core Audio sessions,
    Bluetooth pairing state, collector errors, and rule-derived ``findings``.

    Returns:
        Schema v4 snapshot dict with ``created_at``, ``system``, ``services``,
        ``portaudio``, ``core_audio``, ``bluetooth``, ``errors``, and
        ``findings`` keys.
    """
    errors: list[dict[str, str]] = []
    services, service_errors = _collect_audio_services()
    portaudio, portaudio_errors = _collect_portaudio()
    core_audio, core_errors = _collect_core_audio()
    default_endpoint_name = (core_audio.get("default_endpoint") or {}).get("name")
    bluetooth, bluetooth_errors = collect_bluetooth(default_endpoint_name)
    errors.extend(service_errors)
    errors.extend(portaudio_errors)
    errors.extend(core_errors)
    errors.extend(bluetooth_errors)

    snapshot: dict[str, Any] = {
        "schema_version": 4,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "system": {
            "platform": platform.platform(),
            "windows_release": platform.release(),
            "windows_version": platform.version(),
            "python_version": platform.python_version(),
            "is_windows": sys.platform == "win32",
        },
        "services": services,
        "portaudio": portaudio,
        "core_audio": core_audio,
        "bluetooth": bluetooth,
        "errors": errors,
    }
    snapshot["findings"] = analyze_snapshot(snapshot)
    return snapshot


def _finding(
    severity: str, code: str, title: str, detail: str, action: str
) -> dict[str, str]:
    """Build a standardized finding record for GUI and inference layers."""
    return {
        "severity": severity,
        "code": code,
        "title": title,
        "detail": detail,
        "action": action,
    }


def _device_words(value: str | None) -> set[str]:
    """Extract significant lowercase tokens from a device name for fuzzy match."""
    if not value:
        return set()
    ignored = {
        "audio",
        "default",
        "device",
        "headphones",
        "high",
        "output",
        "primary",
        "sound",
        "speakers",
        "stereo",
    }
    words = {
        word
        for word in re.findall(r"[a-z0-9]+", value.casefold())
        if len(word) > 1 and word not in ignored
    }
    return words


def likely_same_device(first: str | None, second: str | None) -> bool:
    """Heuristically decide whether two endpoint names refer to the same device.

    Uses substring containment and token overlap so minor naming differences
    between Core Audio and PortAudio labels still match (e.g. headset suffixes).

    Args:
        first: First device or endpoint label.
        second: Second device or endpoint label.

    Returns:
        True when names appear to describe the same physical output; also True
        when either name is missing (insufficient data to disagree).
    """
    if not first or not second:
        return True
    first_folded = first.casefold()
    second_folded = second.casefold()
    if first_folded in second_folded or second_folded in first_folded:
        return True
    first_words = _device_words(first)
    second_words = _device_words(second)
    if not first_words or not second_words:
        return True
    overlap = first_words & second_words
    return len(overlap) >= min(2, len(first_words), len(second_words))


def analyze_snapshot(snapshot: dict[str, Any]) -> list[dict[str, str]]:
    """Derive sorted, actionable findings from a snapshot without re-collecting.

    Rules cover platform support, service health, master volume, app vs Windows
    default mismatch, browser session visibility/routing, Bluetooth adapter and
    pairing desync, and partial-scan errors.

    Args:
        snapshot: Dict from :func:`collect_snapshot` or compatible schema.

    Returns:
        Findings sorted by severity (critical first) then title.
    """
    findings: list[dict[str, str]] = []
    system = snapshot.get("system", {})
    services = snapshot.get("services", [])
    portaudio = snapshot.get("portaudio", {})
    core_audio = snapshot.get("core_audio", {})

    if not system.get("is_windows", False):
        findings.append(
            _finding(
                "critical",
                "unsupported-platform",
                "This checker needs Windows",
                "Windows Core Audio is not available on this operating system.",
                "Run the checker on the Windows computer with the silent headphones.",
            )
        )

    stopped_services = [
        service.get("friendly_name", service.get("name", "Audio service"))
        for service in services
        if str(service.get("status", "")).casefold() != "running"
    ]
    if stopped_services:
        findings.append(
            _finding(
                "critical",
                "audio-service-stopped",
                "A Windows audio service is not running",
                ", ".join(stopped_services),
                "Open Services and restart Windows Audio and Windows Audio Endpoint Builder.",
            )
        )
    elif services:
        findings.append(
            _finding(
                "ok",
                "audio-services-running",
                "Windows audio services are running",
                "The core Windows audio services responded normally.",
                "No service action is needed.",
            )
        )

    master_muted = core_audio.get("master_muted")
    master_volume = core_audio.get("master_volume")
    if master_muted or (
        isinstance(master_volume, (int, float)) and master_volume <= 0.02
    ):
        findings.append(
            _finding(
                "critical",
                "master-muted",
                "The headphone output is muted or nearly silent",
                f"Master volume: {float(master_volume or 0) * 100:.0f}%; muted: {bool(master_muted)}.",
                "Unmute the output and raise its volume in Sound settings.",
            )
        )
    elif isinstance(master_volume, (int, float)) and master_volume < 0.20:
        findings.append(
            _finding(
                "warning",
                "master-volume-low",
                "Master headphone volume is low",
                f"Master volume is {master_volume * 100:.0f}% and is not muted.",
                "Raise the headphone volume in Sound settings or on the headset.",
            )
        )
    elif isinstance(master_volume, (int, float)):
        findings.append(
            _finding(
                "ok",
                "master-volume-ok",
                "Master headphone volume is available",
                f"Master volume is {master_volume * 100:.0f}% and is not muted.",
                "No master-volume action is needed.",
            )
        )

    output_devices = portaudio.get("output_devices", [])
    if not output_devices:
        findings.append(
            _finding(
                "critical",
                "no-output-devices",
                "No playback device was available to normal apps",
                "The app-level playback library could not see an output device.",
                "Reconnect the headphones, then update or reinstall the audio driver.",
            )
        )
    else:
        default_name = portaudio.get("default_output_name")
        findings.append(
            _finding(
                "ok",
                "app-output-available",
                "Normal apps can see a playback device",
                f"Default app output: {default_name or 'Windows default'}.",
                "Use the Test app sound button to verify this path.",
            )
        )

    endpoint = core_audio.get("default_endpoint") or {}
    core_name = endpoint.get("name")
    app_name = portaudio.get("default_output_name")
    if core_name and app_name and not likely_same_device(core_name, app_name):
        findings.append(
            _finding(
                "warning",
                "default-device-mismatch",
                "Windows and normal apps may be using different outputs",
                f"Windows endpoint: {core_name}; app endpoint: {app_name}.",
                "Open Volume mixer and select the headphones for the affected app.",
            )
        )

    sessions = core_audio.get("sessions", [])
    browser_sessions = [
        session
        for session in sessions
        if session.get("is_browser")
        or str(session.get("process", "")).casefold() in BROWSER_PROCESSES
    ]
    silent_browser_sessions = [
        session
        for session in browser_sessions
        if session.get("muted")
        or float(session.get("volume", 1.0) or 0.0) <= 0.02
    ]

    def _session_label(session: dict[str, Any]) -> str:
        volume_pct = float(session.get("volume", 0) or 0) * 100
        label = f"{session.get('process')} ({volume_pct:.0f}%)"
        output = session.get("output_device")
        if output:
            label = f"{label} on {output}"
        if session.get("muted"):
            label = f"{label}, muted"
        return label

    if silent_browser_sessions:
        labels = ", ".join(_session_label(session) for session in silent_browser_sessions)
        findings.append(
            _finding(
                "critical",
                "browser-session-silent",
                "A browser audio session is muted or at zero",
                labels,
                "Use Unmute browser sessions, then retry YouTube.",
            )
        )
    elif browser_sessions:
        labels = ", ".join(_session_label(session) for session in browser_sessions)
        findings.append(
            _finding(
                "ok",
                "browser-session-visible",
                "Windows can see the browser audio session",
                labels,
                "If it is still silent, check its Output device in Volume mixer.",
            )
        )
    else:
        findings.append(
            _finding(
                "warning",
                "browser-session-missing",
                "No browser audio session was visible",
                "Browsers usually appear only after a tab starts playing sound.",
                "Start a YouTube video, leave it playing, and select Scan again.",
            )
        )

    default_endpoint_name = core_name or (endpoint.get("name") if endpoint else None)
    mismatched_browser_sessions = [
        session
        for session in browser_sessions
        if session.get("output_device")
        and default_endpoint_name
        and not likely_same_device(
            str(session.get("output_device")), str(default_endpoint_name)
        )
    ]
    browsers_on_default = [
        session
        for session in browser_sessions
        if session.get("output_device")
        and default_endpoint_name
        and likely_same_device(
            str(session.get("output_device")), str(default_endpoint_name)
        )
    ]

    def _is_active_session(session: dict[str, Any]) -> bool:
        state = str(session.get("state", "")).casefold()
        return state in {"1", "active", "audiosessionstate.active"}

    active_mismatched = [
        session
        for session in mismatched_browser_sessions
        if _is_active_session(session)
    ]
    # Warn when live audio is clearly on the wrong device, or when every
    # browser path is off the default headphones (stale dual registrations alone
    # are common and not enough to alarm).
    should_warn_mismatch = bool(active_mismatched) or (
        bool(mismatched_browser_sessions) and not browsers_on_default
    )
    if should_warn_mismatch:
        report_sessions = active_mismatched or mismatched_browser_sessions
        labels = ", ".join(_session_label(session) for session in report_sessions)
        findings.append(
            _finding(
                "warning",
                "browser-output-mismatch",
                "A browser is playing through a different output",
                (
                    f"Default headphones: {default_endpoint_name}. "
                    f"Browser path(s): {labels}."
                ),
                "Open Volume mixer and set the browser Output to Default or your headphones.",
            )
        )

    if len(output_devices) > 1:
        findings.append(
            _finding(
                "info",
                "multiple-outputs",
                "More than one playback path is installed",
                f"{len(output_devices)} app-level outputs were found across Windows audio APIs.",
                "This is normal, but it makes per-app routing the most likely cause.",
            )
        )

    bluetooth = snapshot.get("bluetooth") or {}
    disabled_adapters = disabled_bluetooth_adapters(snapshot)
    if disabled_adapters:
        labels = ", ".join(
            f"{item.get('name')} [{item.get('status')}"
            f"/code={item.get('problem_code')}]"
            for item in disabled_adapters
        )
        findings.append(
            _finding(
                "critical",
                "bluetooth-adapter-disabled",
                "Bluetooth adapter is disabled (Add device will fail)",
                labels,
                "Use Enable Bluetooth adapter (Admin/UAC), then retry Add device. This is the usual cause of Windows 'Couldn't connect'.",
            )
        )

    association = bluetooth.get("association_service") or {}
    association_status = str(association.get("status", "")).casefold()
    if association_status in {"stopped", "stoppending", "stop pending"}:
        findings.append(
            _finding(
                "warning",
                "bluetooth-association-service",
                "Device Association Service is not running",
                f"Status: {association.get('status')}.",
                "Use Repair Bluetooth pairing, then reboot. Bluetooth Settings can get stuck Removing device while this service is down.",
            )
        )

    paired_headsets = list(bluetooth.get("paired_headsets") or [])
    if len(paired_headsets) > 1:
        labels = ", ".join(str(item.get("name")) for item in paired_headsets[:5])
        findings.append(
            _finding(
                "info",
                "bluetooth-multiple-headsets",
                "Multiple Bluetooth headsets are paired",
                labels,
                "Extra paired headsets make Windows tray/icon status more unreliable.",
            )
        )

    endpoint_name = core_name or (endpoint.get("name") if endpoint else None)
    matched_headset = match_headset_for_endpoint(bluetooth, endpoint_name)
    endpoint_present = bluetooth.get("default_endpoint_present")
    looks_like_bt_default = bool(matched_headset) or (
        isinstance(endpoint_name, str)
        and any(
            token in endpoint_name.casefold()
            for token in ("edifier", "bluetooth", "headset", "airpods", "buds")
        )
    )
    if looks_like_bt_default and endpoint_present is False:
        headset_label = (
            f"{matched_headset.get('name')} ({matched_headset.get('address')})"
            if matched_headset
            else endpoint_name
        )
        findings.append(
            _finding(
                "warning",
                "bluetooth-audio-ui-desync",
                "Bluetooth audio works, but Windows status looks disconnected",
                (
                    f"Default playback is {endpoint_name}, but the audio endpoint "
                    f"reports IsPresent=False. Matched headset: {headset_label}."
                ),
                "Use Repair Bluetooth pairing (clears pairing cache; needs Admin + reboot), then re-pair the headset.",
            )
        )
    elif matched_headset and not matched_headset.get("last_connected"):
        findings.append(
            _finding(
                "warning",
                "bluetooth-audio-ui-desync",
                "Bluetooth audio works, but Windows status looks disconnected",
                (
                    f"Default playback is {endpoint_name}, and paired headset "
                    f"{matched_headset.get('name')} has no LastConnectedTime."
                ),
                "Use Repair Bluetooth pairing (clears pairing cache; needs Admin + reboot), then re-pair the headset.",
            )
        )

    errors = snapshot.get("errors", [])
    if errors:
        findings.append(
            _finding(
                "warning",
                "partial-scan",
                "Part of the scan could not be completed",
                "; ".join(
                    f"{item.get('source')}: {item.get('message')}" for item in errors[:3]
                ),
                "Save the report and include it when opening a GitHub issue.",
            )
        )

    return sorted(
        findings,
        key=lambda item: (
            SEVERITY_ORDER.get(item.get("severity", "info"), 99),
            item.get("title", ""),
        ),
    )


def output_device_choices(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """List PortAudio outputs suitable for the GUI test-tone picker.

    Prefers WASAPI host devices when present; otherwise returns all outputs.

    Args:
        snapshot: Scan dict containing ``portaudio.output_devices``.

    Returns:
        Device dicts with ``index``, ``name``, ``host_api``, and related fields.
    """
    devices = snapshot.get("portaudio", {}).get("output_devices", [])
    wasapi = [
        device
        for device in devices
        if "wasapi" in str(device.get("host_api", "")).casefold()
    ]
    return wasapi or devices


def play_test_tone(device_index: int | None = None, seconds: float = 2.0) -> str:
    """Play a quiet test tone through an app-level output path.

    Uses non-blocking playback with a short fade envelope to avoid clicks.
    Validates the PortAudio/app path independently of Windows tray UI state.
    Stereo when the selected device reports ≥2 output channels; otherwise mono.

    Args:
        device_index: PortAudio output index; ``None`` uses the default device.
        seconds: Tone duration.

    Returns:
        Human-readable confirmation naming the selected output device.
    """
    import numpy as np
    import sounddevice as sd

    device = sd.query_devices(device_index, "output")
    sample_rate = int(float(device.get("default_samplerate", 44100)))
    sample_rate = sample_rate if sample_rate > 0 else 44100
    channels = 2 if int(device.get("max_output_channels", 1)) >= 2 else 1
    frame_count = int(sample_rate * seconds)
    time_axis = np.arange(frame_count, dtype=np.float64) / sample_rate
    fade_frames = max(1, int(sample_rate * 0.04))
    envelope = np.ones(frame_count, dtype=np.float64)
    fade = np.linspace(0.0, 1.0, fade_frames, endpoint=True)
    envelope[:fade_frames] = fade
    envelope[-fade_frames:] = fade[::-1]

    left = 0.12 * np.sin(2 * np.pi * 440.0 * time_axis) * envelope
    if channels == 2:
        right = 0.12 * np.sin(2 * np.pi * 660.0 * time_axis) * envelope
        data = np.column_stack((left, right)).astype(np.float32)
    else:
        data = left.astype(np.float32)

    sd.stop()
    sd.play(data, sample_rate, device=device_index, blocking=False)
    return (
        f"Playing a {seconds:.0f}-second app sound through "
        f"{device.get('name', 'the selected output')}."
    )


def stop_test_tone() -> None:
    """Stop any in-progress PortAudio test tone started by :func:`play_test_tone`."""
    import sounddevice as sd

    sd.stop()


def unmute_silent_browser_sessions(minimum_volume: float = 0.5) -> list[str]:
    """Unmute only recognized browser sessions and raise very low ones.

    Targets processes listed in :data:`BROWSER_PROCESSES`; does not alter
    non-browser app sessions.

    Args:
        minimum_volume: Volume scalar passed to pycaw when raising quiet
            sessions. Callers should pass a value in ``[0.0, 1.0]``; this
            function does not clamp out-of-range inputs.

    Returns:
        Descriptions of sessions that were changed.

    Raises:
        RuntimeError: When not running on Windows.
    """
    if sys.platform != "win32":
        raise RuntimeError("Browser audio sessions are available only on Windows.")

    changed: list[str] = []
    _, co_uninitialize = _init_com()
    try:
        from pycaw.pycaw import AudioUtilities

        for session in AudioUtilities.GetAllSessions():
            process_name = _friendly_process_name(session)
            if process_name.casefold() not in BROWSER_PROCESSES:
                continue
            simple_volume = session.SimpleAudioVolume
            was_muted = bool(simple_volume.GetMute())
            old_volume = float(simple_volume.GetMasterVolume())
            if was_muted:
                simple_volume.SetMute(0, None)
            if old_volume < minimum_volume:
                simple_volume.SetMasterVolume(minimum_volume, None)
            if was_muted or old_volume < minimum_volume:
                changed.append(
                    f"{process_name}: mute={was_muted}, "
                    f"{old_volume * 100:.0f}% -> {max(old_volume, minimum_volume) * 100:.0f}%"
                )
    finally:
        if co_uninitialize is not None:
            try:
                co_uninitialize()
            except Exception:
                pass
    return changed


def open_windows_settings(uri: str) -> None:
    """Open a Windows Settings deep link (``ms-settings:...`` URI).

    Args:
        uri: Settings protocol URI understood by ``os.startfile``.

    Raises:
        RuntimeError: When not running on Windows.
    """
    if sys.platform != "win32":
        raise RuntimeError("Windows Settings links are available only on Windows.")
    os.startfile(uri)  # type: ignore[attr-defined]


def save_report(snapshot: dict[str, Any], path: str | Path) -> Path:
    """Write a snapshot dict to JSON on disk for support or GitHub issues.

    Args:
        snapshot: Full scan payload to persist.
        path: Destination file path.

    Returns:
        Resolved path of the written file.
    """
    destination = Path(path)
    destination.write_text(
        json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return destination


def browser_processes() -> Iterable[str]:
    """Return sorted executable names treated as browsers for session rules."""
    return sorted(BROWSER_PROCESSES)


