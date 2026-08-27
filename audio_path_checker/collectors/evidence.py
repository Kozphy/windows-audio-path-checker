"""Structured evidence collection for the Bluetooth headset audio path.

Builds a normalized, read-only evidence document from multiple sources:

1. **WinRT capability probe** (``platform.winrt.probe_winrt_capabilities``) —
   discovery API availability before any pairing attempt.
2. **PowerShell collector** (``scripts/Collectors/Evidence.ps1`` on Windows) —
   Bluetooth adapter, device pair/connect, PnP nodes, audio endpoints, services.
3. **Optional Python snapshot** — Core Audio defaults and service status from
   an existing collector when injected by the caller.

The merged document flows to ``evidence_feature_vector`` for classification.
Collection never restarts services or mutates device state.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..platform.winrt import probe_winrt_capabilities


def _scripts_root() -> Path:
    """Return the repository ``scripts/`` directory path.

    Returns:
        Absolute path to the ``scripts`` folder at the repo root.
    """
    return Path(__file__).resolve().parents[2] / "scripts"


def _service_status_map(services: dict[str, Any] | None) -> dict[str, str]:
    """Coerce service status values to plain strings.

    Args:
        services: Raw ``services`` section from evidence or the PS collector.

    Returns:
        Mapping of service name → status string (empty dict if input invalid).
    """
    out: dict[str, str] = {}
    if not isinstance(services, dict):
        return out
    for key, value in services.items():
        out[str(key)] = str(value)
    return out


def _enrich_from_snapshot(
    evidence: dict[str, Any], snapshot: dict[str, Any] | None
) -> dict[str, Any]:
    """Merge Core Audio defaults and service status from a Python snapshot.

    Args:
        evidence: In-progress evidence document to update in place.
        snapshot: Optional legacy collector output with ``core_audio`` and
            ``services`` sections.

    Returns:
        The same ``evidence`` dict with ``audio`` and ``services`` enriched.
    """
    if not snapshot:
        return evidence
    core = snapshot.get("core_audio") or {}
    default_ep = core.get("default_endpoint") or {}
    audio = dict(evidence.get("audio") or {})
    default_name = str(default_ep.get("name") or "")
    audio["default_playback_name"] = default_name or None
    audio["master_volume"] = core.get("master_volume")
    audio["master_muted"] = core.get("master_muted")

    device_name = str(
        (evidence.get("environment") or {}).get("device_filter")
        or (evidence.get("device") or {}).get("name")
        or ""
    )
    is_default = None
    if default_name and device_name:
        is_default = device_name.casefold() in default_name.casefold()
    elif default_name:
        is_default = False
    audio["is_default_playback"] = is_default
    evidence["audio"] = audio

    # Audio services from Python snapshot if PS missed them
    svc = dict(evidence.get("services") or {})
    for item in snapshot.get("services") or []:
        name = str(item.get("name") or "")
        if name in {"Audiosrv", "AudioEndpointBuilder"}:
            svc[name] = str(item.get("status") or "unknown")
    evidence["services"] = svc
    return evidence


def collect_evidence(
    *,
    device_name: str = "EDIFIER W800BT Pro",
    snapshot: dict[str, Any] | None = None,
    include_winrt_probe: bool = True,
    timeout: int = 90,
) -> dict[str, Any]:
    """Collect normalized Bluetooth audio-path evidence (read-only).

    On Windows, invokes ``Evidence.ps1`` and optionally the WinRT probe. On
    non-Windows platforms, returns the default stub and only merges selected
    ``core_audio`` / ``services`` fields from ``snapshot`` (not a full evidence
    substitute unless tests build those keys carefully).

    Args:
        device_name: Friendly name filter for the target headset.
        snapshot: Optional Python-side collector output to merge after PS
            collection (Core Audio defaults, Audiosrv status).
        include_winrt_probe: When True, attach WinRT discovery capability to
            ``evidence["capabilities"]``.
        timeout: Maximum seconds to wait for the PowerShell collector.

    Returns:
        Evidence document with keys ``device``, ``bluetooth``, ``pnp``,
        ``audio``, ``services``, ``environment``, ``capabilities``, and
        ``collection_errors``. Errors are non-fatal and recorded in
        ``collection_errors``.

    Notes:
        Never restarts services or changes pairing state. Python WinRT probe
        results take precedence over duplicate fields from PowerShell output.
    """
    evidence: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "device": {
            "name": device_name,
            "paired": False,
            "connected": False,
            "address": None,
            "instance_id": None,
            "status": None,
            "last_connected": None,
        },
        "bluetooth": {
            "adapter_present": False,
            "adapter_enabled": False,
            "adapter_status": None,
            "adapter_name": None,
            "adapter_instance_id": None,
            "adapter_driver": None,
            "adapters": [],
        },
        "pnp": {
            "nodes": [],
            "a2dp_nodes": [],
            "media_nodes": [],
            "endpoint_nodes": [],
        },
        "audio": {
            "media_node_present": False,
            "a2dp_present": False,
            "endpoint_present": False,
            "endpoint_active": False,
            "endpoints": [],
            "default_playback_name": None,
            "is_default_playback": None,
            "master_volume": None,
            "master_muted": None,
        },
        "services": {
            "bthserv": "unknown",
            "BTAGService": "unknown",
            "BthAvctpSvc": "unknown",
            "DeviceAssociationService": "unknown",
            "Audiosrv": "unknown",
            "AudioEndpointBuilder": "unknown",
        },
        "environment": {
            "device_filter": device_name,
            "is_windows": sys.platform == "win32",
        },
        "capabilities": None,
        "collection_errors": [],
    }

    if include_winrt_probe:
        try:
            evidence["capabilities"] = probe_winrt_capabilities()
        except Exception as exc:  # noqa: BLE001
            evidence["collection_errors"].append(
                {
                    "source": "winrt_probe",
                    "type": type(exc).__name__,
                    "message": str(exc),
                }
            )

    if sys.platform != "win32":
        return _enrich_from_snapshot(evidence, snapshot)

    script = _scripts_root() / "Collectors" / "Evidence.ps1"
    if not script.is_file():
        evidence["collection_errors"].append(
            {
                "source": "evidence_script",
                "type": "FileNotFoundError",
                "message": str(script),
            }
        )
        return _enrich_from_snapshot(evidence, snapshot)

    env = os.environ.copy()
    try:
        completed = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
                "-DeviceName",
                device_name,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
            check=False,
        )
        raw = (completed.stdout or "").strip()
        if raw:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                # Preserve Python winrt probe as authoritative when present
                caps = evidence.get("capabilities")
                evidence.update(parsed)
                if caps is not None:
                    evidence["capabilities"] = caps
        elif completed.returncode != 0:
            evidence["collection_errors"].append(
                {
                    "source": "evidence_script",
                    "type": "RuntimeError",
                    "message": (completed.stderr or f"exit {completed.returncode}")[
                        :500
                    ],
                }
            )
    except Exception as exc:  # noqa: BLE001
        evidence["collection_errors"].append(
            {
                "source": "evidence_script",
                "type": type(exc).__name__,
                "message": str(exc),
            }
        )

    evidence["services"] = _service_status_map(evidence.get("services"))
    return _enrich_from_snapshot(evidence, snapshot)


def evidence_feature_vector(evidence: dict[str, Any]) -> dict[str, Any]:
    """Derive boolean and scalar features from a raw evidence document.

    Used by ``classify_state``, invariant checks, and diagnosis providers to
    avoid re-implementing nested evidence lookups.

    Args:
        evidence: Output of :func:`collect_evidence`.

    Returns:
        Flat feature dict (adapter, pairing, A2DP, endpoint, service health,
        WinRT availability).

    Notes:
        Most flags use ``bool(...)`` and collapse missing→False. ``is_default_playback``
        preserves ``None`` (UNKNOWN) so CLI/check status can distinguish unset from
        explicitly false.
    """
    device = evidence.get("device") or {}
    bluetooth = evidence.get("bluetooth") or {}
    audio = evidence.get("audio") or {}
    services = evidence.get("services") or {}
    caps = evidence.get("capabilities") or {}

    def _running(name: str) -> bool:
        return str(services.get(name, "")).casefold() == "running"

    return {
        "adapter_present": bool(bluetooth.get("adapter_present")),
        "adapter_enabled": bool(bluetooth.get("adapter_enabled")),
        "device_paired": bool(device.get("paired")),
        "device_connected": bool(device.get("connected")),
        "a2dp_present": bool(audio.get("a2dp_present")),
        "media_node_present": bool(audio.get("media_node_present")),
        "endpoint_present": bool(audio.get("endpoint_present")),
        "endpoint_active": bool(audio.get("endpoint_active")),
        "is_default_playback": audio.get("is_default_playback"),
        "audio_services_healthy": _running("Audiosrv")
        and _running("AudioEndpointBuilder"),
        "bt_services_healthy": _running("bthserv") and _running("BthAvctpSvc"),
        "winrt_discovery_available": bool(caps.get("available")),
        "device_name": device.get("name"),
    }
