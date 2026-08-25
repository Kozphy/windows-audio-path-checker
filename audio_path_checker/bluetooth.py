"""Bluetooth headset status and opt-in pairing repair for Windows."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


_BT_COLLECT_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
$result = [ordered]@{
  association_service = $null
  bluetooth_service = $null
  paired_headsets = @()
  default_endpoint_present = $null
  default_endpoint_name = $null
}
foreach ($name in @('DeviceAssociationService','bthserv')) {
  $svc = Get-Service -Name $name -ErrorAction SilentlyContinue
  if ($svc) {
    $entry = [ordered]@{ name = $svc.Name; status = [string]$svc.Status }
    if ($name -eq 'DeviceAssociationService') { $result.association_service = $entry }
    else { $result.bluetooth_service = $entry }
  }
}
$headsets = @()
Get-PnpDevice -Class Bluetooth -ErrorAction SilentlyContinue |
  Where-Object {
    $_.FriendlyName -and
    $_.FriendlyName -notmatch 'Avrcp|Enumerator|RFCOMM|Radio|Adapter|LE Enumerator|Protocol'
  } |
  ForEach-Object {
    $props = Get-PnpDeviceProperty -InstanceId $_.InstanceId -ErrorAction SilentlyContinue
    $addr = ($props | Where-Object KeyName -eq 'DEVPKEY_Bluetooth_DeviceAddress').Data
    $last = ($props | Where-Object KeyName -eq 'DEVPKEY_Bluetooth_LastConnectedTime').Data
    $present = ($props | Where-Object KeyName -eq 'DEVPKEY_Device_IsPresent').Data
    $category = ($props | Where-Object KeyName -eq 'DEVPKEY_DeviceContainer_Category').Data
    $isHeadset = $false
    if ($category -match 'Headset|Headphones|Audio') { $isHeadset = $true }
    elseif ($_.FriendlyName -match 'EDIFIER|Headphone|Headset|AirPods|Buds|Soundcore|Sony|Bose|JBL|WH-|W\d|BT') { $isHeadset = $true }
    if (-not $isHeadset) { return }
    $headsets += [ordered]@{
      name = [string]$_.FriendlyName
      status = [string]$_.Status
      instance_id = [string]$_.InstanceId
      address = if ($addr) { ([string]$addr).ToLowerInvariant() } else { $null }
      last_connected = if ($last) { [string]$last } else { $null }
      is_present = [bool]$present
      category = if ($category) { [string]$category } else { $null }
    }
  }
$result.paired_headsets = $headsets

$defaultName = $env:WAPC_DEFAULT_ENDPOINT
if ($defaultName) {
  $result.default_endpoint_name = $defaultName
  $ep = Get-PnpDevice -ErrorAction SilentlyContinue |
    Where-Object { $_.Class -eq 'AudioEndpoint' -and $_.FriendlyName -eq $defaultName } |
    Select-Object -First 1
  if ($ep) {
    $present = (Get-PnpDeviceProperty -InstanceId $ep.InstanceId |
      Where-Object KeyName -eq 'DEVPKEY_Device_IsPresent').Data
    $result.default_endpoint_present = [bool]$present
  }
}
$result | ConvertTo-Json -Compress -Depth 6
"""


_BT_REPAIR_SCRIPT_TEMPLATE = r"""
$ErrorActionPreference = 'Continue'
$log = Join-Path $env:TEMP 'wapc-bluetooth-repair.log'
function Log([string]$m) {{ Add-Content -Path $log -Value $m -Encoding UTF8; Write-Output $m }}
Set-Content -Path $log -Value 'Windows Audio Path Checker Bluetooth repair...' -Encoding UTF8
$address = '{address}'
$friendly = '{friendly}'

# Unstick Device Association Service if StopPending
$svc = Get-CimInstance Win32_Service -Filter "Name='DeviceAssociationService'"
if ($svc -and $svc.State -match 'Stop' -and $svc.ProcessId -gt 0) {{
  Log ("Killing stuck DeviceAssociationService PID " + $svc.ProcessId)
  try {{ Stop-Process -Id $svc.ProcessId -Force -ErrorAction Stop; Start-Sleep -Seconds 2; Log 'Killed stuck service process' }}
  catch {{ Log $_.Exception.Message }}
}}
try {{ Start-Service DeviceAssociationService -ErrorAction Stop; Log 'DeviceAssociationService running' }}
catch {{ Log ('DeviceAssociationService start: ' + $_.Exception.Message) }}

$deleted = 0
$devices = 'HKLM:\SYSTEM\CurrentControlSet\Services\BTHPORT\Parameters\Devices'
if (Test-Path $devices) {{
  Get-ChildItem $devices | Where-Object {{ $_.PSChildName -eq $address }} | ForEach-Object {{
    try {{ Remove-Item $_.PSPath -Recurse -Force; Log ('Deleted pairing device key ' + $_.PSChildName); $deleted++ }}
    catch {{ Log $_.Exception.Message }}
  }}
}}
$keys = 'HKLM:\SYSTEM\CurrentControlSet\Services\BTHPORT\Parameters\Keys'
if (Test-Path $keys) {{
  Get-ChildItem $keys | ForEach-Object {{
    Get-ChildItem $_.PSPath -ErrorAction SilentlyContinue |
      Where-Object {{ $_.PSChildName -eq $address }} |
      ForEach-Object {{
        try {{ Remove-Item $_.PSPath -Recurse -Force; Log ('Deleted link key for ' + $address); $deleted++ }}
        catch {{ Log $_.Exception.Message }}
      }}
  }}
}}
Log ("Registry entries deleted: $deleted")
Log ("Target headset: $friendly ($address)")
Log 'Skipped PnP Disable/Remove (those calls hang when Windows is stuck Removing device).'
Log 'DONE'
Log 'Reboot Windows now. After reboot, re-pair the headset in Bluetooth settings.'
try {{ Start-Process 'ms-settings:bluetooth' }} catch {{}}
"""


def _run_powershell(script: str, *, env: dict[str, str] | None = None, timeout: int = 45) -> str:
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
        env=env,
        check=False,
    )
    if completed.returncode != 0 and not completed.stdout.strip():
        message = completed.stderr.strip() or f"PowerShell exited {completed.returncode}"
        raise RuntimeError(message)
    return completed.stdout.strip()


def collect_bluetooth(
    default_endpoint_name: str | None = None,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Collect paired headset status and association-service health."""
    result: dict[str, Any] = {
        "association_service": None,
        "bluetooth_service": None,
        "paired_headsets": [],
        "default_endpoint_present": None,
        "default_endpoint_name": default_endpoint_name,
    }
    errors: list[dict[str, str]] = []
    if sys.platform != "win32":
        return result, errors

    env = os.environ.copy()
    if default_endpoint_name:
        env["WAPC_DEFAULT_ENDPOINT"] = default_endpoint_name
    try:
        raw = _run_powershell(_BT_COLLECT_SCRIPT, env=env, timeout=60)
        if not raw:
            return result, errors
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            result.update(parsed)
    except Exception as exc:  # noqa: BLE001 - surface as scan error
        errors.append(
            {
                "source": "Bluetooth status",
                "type": type(exc).__name__,
                "message": str(exc),
            }
        )
    return result, errors


def match_headset_for_endpoint(
    bluetooth: dict[str, Any], endpoint_name: str | None
) -> dict[str, Any] | None:
    """Pick the paired headset that best matches a Core Audio endpoint name."""
    if not endpoint_name:
        return None
    headsets = list(bluetooth.get("paired_headsets") or [])
    if not headsets:
        return None

    endpoint_folded = endpoint_name.casefold()
    for item in headsets:
        name = str(item.get("name", "")).casefold()
        if name and (name in endpoint_folded or endpoint_folded in name):
            return item

    ignored = {"headphones", "headset", "audio", "hands", "free", "pro", "bt"}
    endpoint_words = {
        word
        for word in re.findall(r"[a-z0-9]+", endpoint_folded)
        if len(word) > 1 and word not in ignored
    }
    best = None
    best_score = 0
    for item in headsets:
        words = {
            word
            for word in re.findall(r"[a-z0-9]+", str(item.get("name", "")).casefold())
            if len(word) > 1 and word not in ignored
        }
        score = len(endpoint_words & words)
        if score > best_score:
            best = item
            best_score = score
    return best if best_score > 0 else None


def bluetooth_repair_targets(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Return paired headsets that can be repaired (need a Bluetooth address)."""
    bluetooth = snapshot.get("bluetooth") or {}
    targets: list[dict[str, Any]] = []
    for item in bluetooth.get("paired_headsets") or []:
        address = str(item.get("address") or "").strip().lower()
        if not address:
            continue
        targets.append(
            {
                "name": item.get("name"),
                "address": address,
                "last_connected": item.get("last_connected"),
                "is_present": item.get("is_present"),
            }
        )
    return targets


def preferred_bluetooth_repair_target(
    snapshot: dict[str, Any],
) -> dict[str, Any] | None:
    """Prefer the headset tied to the current default playback endpoint."""
    bluetooth = snapshot.get("bluetooth") or {}
    endpoint = (snapshot.get("core_audio") or {}).get("default_endpoint") or {}
    matched = match_headset_for_endpoint(bluetooth, endpoint.get("name"))
    if matched and matched.get("address"):
        return {
            "name": matched.get("name"),
            "address": str(matched.get("address")).lower(),
            "last_connected": matched.get("last_connected"),
            "is_present": matched.get("is_present"),
        }
    targets = bluetooth_repair_targets(snapshot)
    return targets[0] if targets else None


def repair_bluetooth_pairing(
    *,
    address: str,
    friendly_name: str,
    elevate: bool = True,
    wait: bool = True,
) -> dict[str, Any]:
    """
    Opt-in repair for stuck Bluetooth remove / icon desync.

    Clears the BTHPORT pairing cache for one device address. Requires UAC when
    elevate=True. Intentionally avoids Disable-PnpDevice / Remove-PnpDevice,
    which hang when Windows Settings is stuck on "Removing device".
    """
    if sys.platform != "win32":
        raise RuntimeError("Bluetooth repair is available only on Windows.")

    normalized = address.strip().lower().replace(":", "").replace("-", "")
    if not normalized or any(ch not in "0123456789abcdef" for ch in normalized):
        raise ValueError(f"Invalid Bluetooth address: {address!r}")

    safe_name = friendly_name.replace("'", "").replace('"', "")
    script = _BT_REPAIR_SCRIPT_TEMPLATE.format(
        address=normalized,
        friendly=safe_name,
    )
    script_path = Path(tempfile.gettempdir()) / "wapc-bluetooth-repair.ps1"
    log_path = Path(tempfile.gettempdir()) / "wapc-bluetooth-repair.log"
    script_path.write_text(script, encoding="utf-8")
    if log_path.exists():
        log_path.unlink()

    if elevate:
        completed = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-Command",
                (
                    f"$p = Start-Process -FilePath 'powershell.exe' -Verb RunAs "
                    f"-ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File','{script_path}') "
                    f"-PassThru -Wait; exit $p.ExitCode"
                ),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180 if wait else 30,
            check=False,
        )
        log_text = (
            log_path.read_text(encoding="utf-8", errors="replace")
            if log_path.exists()
            else ""
        )
        return {
            "elevated": True,
            "address": normalized,
            "friendly_name": friendly_name,
            "exit_code": completed.returncode,
            "log": log_text,
            "reboot_required": True,
            "script_path": str(script_path),
            "log_path": str(log_path),
        }

    raw = _run_powershell(script, timeout=60)
    return {
        "elevated": False,
        "address": normalized,
        "friendly_name": friendly_name,
        "exit_code": 0,
        "log": raw,
        "reboot_required": True,
        "script_path": str(script_path),
        "log_path": str(log_path),
    }
