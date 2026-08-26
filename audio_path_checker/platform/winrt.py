"""Safe WinRT / Bluetooth discovery capability probing."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


def _scripts_root() -> Path:
    return Path(__file__).resolve().parents[2] / "scripts"


def _extract_json_object(raw: str) -> str | None:
    """Return the first top-level JSON object found in mixed console output."""
    text = (raw or "").strip()
    if not text:
        return None
    if text.startswith("{"):
        return text
    # Prefer the last {...} blob (JSON is usually emitted after the report).
    matches = list(re.finditer(r"\{.*\}", text, flags=re.DOTALL))
    if not matches:
        return None
    return matches[-1].group(0)


def probe_winrt_capabilities(*, timeout: int = 30) -> dict[str, Any]:
    """
    Run the PowerShell WinRT capability probe once.

    Returns a structured capability document. Never retries on failure.
    """
    result: dict[str, Any] = {
        "capability": "bluetooth_discovery",
        "available": False,
        "reason": "not_windows",
        "powershell_version": None,
        "capabilities": [],
        "primary_failure": None,
    }
    if sys.platform != "win32":
        return result

    script = _scripts_root() / "Platform" / "WinRT.ps1"
    if not script.is_file():
        result["reason"] = "probe_script_missing"
        result["primary_failure"] = {
            "capability": "bluetooth_discovery",
            "available": False,
            "reason": "probe_script_missing",
            "path": str(script),
        }
        return result

    try:
        completed = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
                "-JsonOnly",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001
        result["reason"] = "probe_execution_failed"
        result["primary_failure"] = {
            "capability": "bluetooth_discovery",
            "available": False,
            "reason": "probe_execution_failed",
            "detail": str(exc),
        }
        return result

    stdout = (completed.stdout or "").strip()
    stderr = (completed.stderr or "").strip()
    raw = _extract_json_object(stdout)
    if not raw:
        result["reason"] = "empty_probe_output"
        result["primary_failure"] = {
            "capability": "bluetooth_discovery",
            "available": False,
            "reason": "empty_probe_output",
            "stderr": stderr[:500],
            "exit_code": completed.returncode,
            "stdout_preview": stdout[:200],
        }
        return result

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        result["reason"] = "invalid_probe_json"
        result["primary_failure"] = {
            "capability": "bluetooth_discovery",
            "available": False,
            "reason": "invalid_probe_json",
            "detail": str(exc),
            "stdout_preview": stdout[:200],
        }
        return result

    if not isinstance(parsed, dict):
        result["reason"] = "invalid_probe_shape"
        return result

    # Accept both the wrapper schema and Get-BluetoothDiscoveryCapability schema.
    available = bool(
        parsed.get("available")
        or parsed.get("bluetooth_discovery")
        or parsed.get("bluetooth_discovery_available")
    )
    primary = parsed.get("primary_failure")
    reason = ""
    if isinstance(primary, dict):
        reason = str(primary.get("reason") or "")
    elif not available:
        reason = str(parsed.get("reason") or "winrt_type_unavailable")

    result.update(
        {
            "available": available,
            "reason": reason if not available else "",
            "capabilities": parsed.get("capabilities") or [],
            "primary_failure": primary,
            "powershell_version": parsed.get("powershell_version"),
            "raw": parsed,
            "detail": parsed.get("detail"),
        }
    )
    if not result["powershell_version"]:
        for cap in result["capabilities"]:
            if isinstance(cap, dict) and cap.get("capability") == "bluetooth_discovery":
                result["powershell_version"] = cap.get("powershell_version")
                break
    if not available and not result["primary_failure"]:
        result["primary_failure"] = {
            "capability": "bluetooth_discovery",
            "available": False,
            "reason": reason or "bluetooth_discovery_api_unusable",
            "detail": parsed.get("detail"),
        }
    return result


def format_capability_console(probe: dict[str, Any]) -> str:
    """Human-readable one-shot discovery capability message (no spam)."""
    available = bool(probe.get("available"))
    lines = ["[DISCOVERY]", f"WinRT DeviceInformation: {'AVAILABLE' if available else 'UNAVAILABLE'}"]
    if not available:
        reason = probe.get("reason") or "unknown"
        lines.extend(
            [
                "",
                "Reason:",
                f"  {reason}",
                "",
                "Auto-pair has been skipped.",
            ]
        )
        failure = probe.get("primary_failure") or {}
        detail = failure.get("detail") if isinstance(failure, dict) else None
        if not detail:
            detail = probe.get("detail")
        if detail:
            lines.extend(["", "Detail:", f"  {detail}"])
    return "\n".join(lines)
