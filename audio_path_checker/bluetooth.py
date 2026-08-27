"""Bluetooth headset status and opt-in repairs for Windows.

This module collects PnP/Win32 Bluetooth health (adapters, paired headsets,
services) and launches elevated repair/auto-pair scripts. It does **not**
infer that audio is working from Bluetooth connection state alone — a headset
can show ``Status=OK`` in PnP while Core Audio endpoints are missing or stale.

Identity safety: repairs and auto-pair target a **normalized Bluetooth MAC
address**, not a brand name or partial string match. Name hints are used only
when no address is configured.

Notes:
    Pairability (``CanPair`` during discovery) is separate from whether Windows
    can enumerate or connect an already-paired device. Use ``collect_bluetooth``
    for post-pair health; use ``add_bluetooth_device`` for discovery/pairing.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

DEFAULT_ADD_BLUETOOTH_NAME = "EDIFIER W800BT Pro"
DEFAULT_ADD_BLUETOOTH_ADDRESS = "c8247887e57c"

_BT_COLLECT_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
$result = [ordered]@{
  association_service = $null
  bluetooth_service = $null
  audio_gateway_service = $null
  avctp_service = $null
  adapters = @()
  paired_headsets = @()
  default_endpoint_present = $null
  default_endpoint_name = $null
}
foreach ($pair in @(
  @{ Name = 'DeviceAssociationService'; Key = 'association_service' },
  @{ Name = 'bthserv'; Key = 'bluetooth_service' },
  @{ Name = 'BTAGService'; Key = 'audio_gateway_service' },
  @{ Name = 'BthAvctpSvc'; Key = 'avctp_service' }
)) {
  $svc = Get-Service -Name $pair.Name -ErrorAction SilentlyContinue
  if ($svc) {
    $result[$pair.Key] = [ordered]@{ name = $svc.Name; status = [string]$svc.Status }
  }
}

$adapters = @()
Get-PnpDevice -ErrorAction SilentlyContinue |
  Where-Object {
    $_.FriendlyName -and (
      $_.FriendlyName -match 'Bluetooth Adapter' -or
      ($_.Class -eq 'Bluetooth' -and $_.FriendlyName -match 'Adapter|Radio')
    )
  } |
  ForEach-Object {
    $instanceId = $_.InstanceId
    $props = Get-PnpDeviceProperty -InstanceId $instanceId -ErrorAction SilentlyContinue
    $problem = ($props | Where-Object KeyName -eq 'DEVPKEY_Device_ProblemCode').Data
    $present = ($props | Where-Object KeyName -eq 'DEVPKEY_Device_IsPresent').Data
    $cim = Get-CimInstance Win32_PnPEntity -ErrorAction SilentlyContinue |
      Where-Object { $_.PNPDeviceID -eq $instanceId } |
      Select-Object -First 1
    $cm = if ($cim) { [string]$cim.ConfigManagerErrorCode } else { $null }
    $adapters += [ordered]@{
      name = [string]$_.FriendlyName
      status = [string]$_.Status
      instance_id = [string]$instanceId
      class = [string]$_.Class
      is_present = [bool]$present
      problem_code = if ($null -ne $problem) { [int]$problem } else { $null }
      config_manager_error = $cm
    }
  }
$result.adapters = $adapters

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


_BT_ENABLE_ADAPTER_SCRIPT = r"""
$ErrorActionPreference = 'Continue'
$log = Join-Path $env:TEMP 'wapc-bluetooth-enable.log'
function Log([string]$m) {{ Add-Content -Path $log -Value $m -Encoding UTF8; Write-Output $m }}
Set-Content -Path $log -Value 'Windows Audio Path Checker: enable Bluetooth adapter...' -Encoding UTF8
$instanceId = '{instance_id}'

$adapter = $null
if ($instanceId) {{
  $adapter = Get-PnpDevice | Where-Object {{ $_.InstanceId -eq $instanceId }} | Select-Object -First 1
}}
if (-not $adapter) {{
  $adapter = Get-PnpDevice | Where-Object {{ $_.FriendlyName -match 'Bluetooth Adapter' }} | Select-Object -First 1
  if ($adapter) {{ $instanceId = $adapter.InstanceId }}
}}
if (-not $adapter) {{ Log 'No Bluetooth adapter found'; exit 2 }}
Log ("Before: Status=$($adapter.Status) Id=$instanceId")

try {{
  Enable-PnpDevice -InstanceId $instanceId -Confirm:$false -ErrorAction Stop
  Log 'Enable-PnpDevice OK'
}} catch {{
  Log ('Enable-PnpDevice failed: ' + $_.Exception.Message)
  $out = & pnputil.exe /enable-device "$instanceId" 2>&1 | Out-String
  Log ('pnputil: ' + ($out.Trim() -replace '\s+', ' '))
}}

Start-Sleep -Seconds 3
foreach ($name in @('bthserv','BTAGService','BthAvctpSvc','DeviceAssociationService')) {{
  try {{ Start-Service $name -ErrorAction Stop; Log ("Started $name") }}
  catch {{ Log ("Start $name: $($_.Exception.Message)") }}
}}
Start-Sleep -Seconds 2
$after = Get-PnpDevice | Where-Object {{ $_.InstanceId -eq $instanceId }} | Select-Object -First 1
$cim = Get-CimInstance Win32_PnPEntity | Where-Object {{ $_.PNPDeviceID -eq $instanceId }} | Select-Object -First 1
Log ("After: Status=$($after.Status) ConfigManagerErrorCode=$($cim.ConfigManagerErrorCode)")
Log 'DONE'
Log 'Retry Add device in Bluetooth settings (put the headset in pairing mode first).'
try {{ Start-Process 'ms-settings:bluetooth' }} catch {{}}
"""


def normalize_bluetooth_address(address: str) -> str:
    """Normalize MAC variants to lowercase hex without separators.

    Used by CLI/GUI add-device and repair entry points to fail fast on junk
    input (short strings previously hung UAC flows). Soft-normalize for
    identity scoring lives in ``bluetooth_pairing.identity`` and returns ``""``.

    Args:
        address: Bluetooth MAC in any common form (``aa:bb:cc:dd:ee:ff``,
            ``AA-BB-CC-DD-EE-FF``, or ≥12 hex digits).

    Returns:
        Lowercase 12-character hex string with no separators. Longer hex
        inputs keep the trailing 12 digits.

    Raises:
        ValueError: If empty, fewer than 12 hex digits after stripping
            ``:``/``-``, or any remaining character is non-hex.
    """
    normalized = address.strip().lower().replace(":", "").replace("-", "")
    if len(normalized) < 12 or any(ch not in "0123456789abcdef" for ch in normalized):
        raise ValueError(f"Invalid Bluetooth address: {address!r}")
    if len(normalized) > 12:
        normalized = normalized[-12:]
    return normalized


def auto_pair_script_path() -> Path:
    """Return the path to the identity-safe elevated auto-pair orchestrator.

    Returns:
        Absolute path to ``scripts/wapc-bt-auto-pair.ps1`` relative to the
        project root (parent of ``audio_path_checker``).
    """
    return Path(__file__).resolve().parents[1] / "scripts" / "wapc-bt-auto-pair.ps1"


def _ps_single_quote(value: str) -> str:
    """Escape a Python string for embedding in a PowerShell single-quoted literal.

    Args:
        value: Raw string to embed in generated PowerShell.

    Returns:
        PowerShell-safe single-quoted literal (``'...'`` with ``''`` escapes).
    """
    return "'" + str(value).replace("'", "''") + "'"


def _run_powershell(script: str, *, env: dict[str, str] | None = None, timeout: int = 45) -> str:
    """Run an inline PowerShell script and return stdout.

    Args:
        script: PowerShell source executed with ``-Command``.
        env: Optional environment overrides passed to the child process.
        timeout: Maximum seconds before ``subprocess.TimeoutExpired``.

    Returns:
        Stripped stdout text. Non-zero process exits are tolerated when stdout
        is non-empty (callers often parse partial JSON / logs).

    Raises:
        RuntimeError: Only when PowerShell exits non-zero **and** stdout is
            empty; the message uses stderr or the exit code.
    """
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


def _run_elevated_script(
    script_path: Path,
    log_path: Path,
    *,
    wait: bool = True,
    extra_args: list[str] | None = None,
    timeout: int | None = None,
) -> tuple[int, str]:
    """Launch a PowerShell script elevated via UAC (``RunAs``).

    Args:
        script_path: ``.ps1`` file passed to ``powershell.exe -File``.
        log_path: Log file read after completion (may be empty if missing).
        wait: If True, block until the elevated process exits; otherwise
            fire-and-forget after UAC approval.
        extra_args: Additional arguments appended after ``-File <script>``.
        timeout: Seconds for the wrapper subprocess; defaults to 180 when
            ``wait`` is True and 30 when False.

    Returns:
        Tuple of ``(exit_code, log_text)`` where ``log_text`` is the contents
        of ``log_path`` when it exists, else an empty string.

    Notes:
        UAC cancellation yields exit code 1 from the wrapper when
        ``Start-Process -PassThru`` returns null.
    """
    arg_items = [
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script_path),
    ]
    if extra_args:
        arg_items.extend(extra_args)
    arg_list = "@(" + ",".join(_ps_single_quote(item) for item in arg_items) + ")"
    if wait:
        command = (
            f"$p = Start-Process -FilePath 'powershell.exe' -Verb RunAs "
            f"-ArgumentList {arg_list} -PassThru -Wait; "
            f"if ($null -eq $p) {{ exit 1 }}; exit $p.ExitCode"
        )
    else:
        command = (
            f"$p = Start-Process -FilePath 'powershell.exe' -Verb RunAs "
            f"-ArgumentList {arg_list} -PassThru; "
            f"if ($null -eq $p) {{ exit 1 }}; exit 0"
        )
    effective_timeout = timeout if timeout is not None else (180 if wait else 30)
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-Command",
            command,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=effective_timeout,
        check=False,
    )
    log_text = (
        log_path.read_text(encoding="utf-8", errors="replace")
        if log_path.exists()
        else ""
    )
    return completed.returncode, log_text


def _read_auto_pair_status() -> dict[str, Any]:
    """Load the auto-pair orchestrator status JSON from the temp directory.

    Returns:
        Parsed status dict, or ``{}`` if the file is missing or invalid JSON.
    """
    status_path = Path(tempfile.gettempdir()) / "wapc-bt-auto-pair-status.json"
    if not status_path.exists():
        return {}
    try:
        raw = json.loads(status_path.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError:
        return {}
    return raw if isinstance(raw, dict) else {}


def add_bluetooth_device(
    *,
    name: str = DEFAULT_ADD_BLUETOOTH_NAME,
    address: str = DEFAULT_ADD_BLUETOOTH_ADDRESS,
    elevate: bool = True,
    discovery_timeout_sec: int = 180,
    diagnostics: bool = True,
    wait: bool = True,
) -> dict[str, Any]:
    """Launch identity-safe elevated auto-pair for one Bluetooth headset.

    Discovery and pairing are gated on **target address** when provided; a
    sibling headset with a similar name must not satisfy success. Pairing
    success does not guarantee Core Audio endpoints are ready — check
    ``overall_result`` and ``classification`` in the returned status.

    Args:
        name: Human-readable target name (hint only when address is set).
        address: Normalized Bluetooth MAC for the exact recovery target.
        elevate: Run the orchestrator elevated (UAC) when True.
        discovery_timeout_sec: WinRT discovery window (minimum 30 seconds).
        diagnostics: Pass ``-Diagnostics`` to the PowerShell orchestrator.
        wait: Block until the elevated script finishes when True.

    Returns:
        Dict with keys ``elevated``, ``target_name``, ``target_address``,
        ``exit_code``, ``log``, ``status``, ``classification``,
        ``overall_result``, ``script_path``, ``log_path``, ``status_path``,
        and ``success``.

    Raises:
        RuntimeError: On non-Windows platforms.
        ValueError: If ``name`` is empty after stripping.
        FileNotFoundError: If ``scripts/wapc-bt-auto-pair.ps1`` is missing.

    Notes:
        Requires the headset to be in **pairing mode** during discovery.
        ``success`` is True when exit code is 0 **or** status reports
        ``SUCCESS`` — audio path verification is handled inside the script.
    """
    if sys.platform != "win32":
        raise RuntimeError("Add Bluetooth device is available only on Windows.")

    target_name = (name or DEFAULT_ADD_BLUETOOTH_NAME).strip()
    if not target_name:
        raise ValueError("Bluetooth device name is required.")
    normalized = normalize_bluetooth_address(address)
    script_path = auto_pair_script_path()
    if not script_path.is_file():
        raise FileNotFoundError(f"Auto-pair script not found: {script_path}")

    log_path = Path(tempfile.gettempdir()) / "wapc-bt-auto-pair.log"
    status_path = Path(tempfile.gettempdir()) / "wapc-bt-auto-pair-status.json"
    extra_args = [
        "-TargetName",
        target_name,
        "-TargetAddress",
        normalized,
        "-DiscoveryTimeoutSec",
        str(max(30, int(discovery_timeout_sec))),
    ]
    if diagnostics:
        extra_args.append("-Diagnostics")

    # Cleanup + discovery window can exceed 3 minutes.
    elevate_timeout = max(240, int(discovery_timeout_sec) + 120) if wait else 30

    if elevate:
        exit_code, log_text = _run_elevated_script(
            script_path,
            log_path,
            wait=wait,
            extra_args=extra_args,
            timeout=elevate_timeout,
        )
    else:
        cmd = [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script_path),
            *extra_args,
        ]
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=elevate_timeout,
            check=False,
        )
        exit_code = completed.returncode
        log_text = (
            log_path.read_text(encoding="utf-8", errors="replace")
            if log_path.exists()
            else (completed.stdout or "") + (completed.stderr or "")
        )

    status = _read_auto_pair_status()
    classification = (
        status.get("classification")
        or status.get("failureClassification")
        or status.get("failure_classification")
    )
    overall = status.get("overall_result") or status.get("finalResult") or status.get("final_result")
    return {
        "elevated": elevate,
        "target_name": target_name,
        "target_address": normalized,
        "exit_code": exit_code,
        "log": log_text,
        "status": status,
        "classification": classification,
        "overall_result": overall,
        "script_path": str(script_path),
        "log_path": str(log_path),
        "status_path": str(status_path),
        "success": exit_code == 0 or str(overall).upper() == "SUCCESS",
    }


def collect_bluetooth(
    default_endpoint_name: str | None = None,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Collect adapter, headset, and Bluetooth service health.

    Reports PnP presence and service status — **not** whether audio playback
    works. A paired headset may appear with ``status=OK`` while the default
    Core Audio endpoint is absent.

    Args:
        default_endpoint_name: Optional playback endpoint name; when set, the
            collector also records ``default_endpoint_present`` for that name.

    Returns:
        Tuple of ``(result, errors)`` where ``result`` contains
        ``association_service``, ``bluetooth_service``, ``audio_gateway_service``,
        ``avctp_service``, ``adapters``, ``paired_headsets``,
        ``default_endpoint_present``, and ``default_endpoint_name``.
        ``errors`` lists non-fatal scan failures.

    Notes:
        On non-Windows platforms returns an empty-shaped ``result`` and no
        errors (no-op).
    """
    result: dict[str, Any] = {
        "association_service": None,
        "bluetooth_service": None,
        "audio_gateway_service": None,
        "avctp_service": None,
        "adapters": [],
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
    """Pick the paired headset that best matches a Core Audio endpoint name.

    Uses fuzzy name overlap — **not** Bluetooth address. Prefer explicit
    address matching (``repair_bluetooth_pairing``, identity module) when the
    MAC is known.

    Args:
        bluetooth: Snapshot dict containing ``paired_headsets``.
        endpoint_name: Core Audio default endpoint friendly name.

    Returns:
        Best-matching headset dict, or ``None`` if no plausible match.
    """
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
    """Return paired headsets eligible for BTHPORT cache repair.

    Args:
        snapshot: Full or partial checker snapshot with a ``bluetooth`` key.

    Returns:
        List of dicts with ``name``, ``address``, ``last_connected``, and
        ``is_present``. Entries without a normalized address are skipped.

    Notes:
        Repair clears pairing registry keys by **address**; name alone is
        insufficient.
    """
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
    """Prefer the headset tied to the current default playback endpoint.

    Args:
        snapshot: Checker snapshot with ``bluetooth`` and ``core_audio`` keys.

    Returns:
        Repair target dict (``name``, ``address``, ``last_connected``,
        ``is_present``) for the endpoint-matched headset, else the first
        address-bearing paired headset, else ``None``.

    Notes:
        Endpoint name matching is a heuristic; address remains authoritative
        for ``repair_bluetooth_pairing``.
    """
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


def disabled_bluetooth_adapters(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """List adapters that are disabled or erroring and block pairing.

    Args:
        snapshot: Checker snapshot with a ``bluetooth`` key.

    Returns:
        Adapter dicts where PnP status is ``error``/``disabled``, problem
        code is 22 (``CM_PROB_DISABLED``), or Config Manager reports disabled.

    Notes:
        A disabled radio prevents discovery entirely — distinct from
        ``NOT_PAIRABLE`` when the adapter works but ``CanPair`` is false.
    """
    bluetooth = snapshot.get("bluetooth") or {}
    bad: list[dict[str, Any]] = []
    for item in bluetooth.get("adapters") or []:
        status = str(item.get("status", "")).casefold()
        problem = item.get("problem_code")
        cm = str(item.get("config_manager_error") or "").casefold()
        disabled = (
            status in {"error", "disabled"}
            or problem == 22  # CM_PROB_DISABLED
            or "disabled" in cm
            or cm == "cm_prob_disabled"
        )
        if disabled and item.get("instance_id"):
            bad.append(item)
    return bad


def preferred_bluetooth_adapter(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    """Choose the adapter most likely needing ``enable_bluetooth_adapter``.

    Args:
        snapshot: Checker snapshot with a ``bluetooth`` key.

    Returns:
        First disabled adapter if any, else the first known adapter, else
        ``None``.
    """
    disabled = disabled_bluetooth_adapters(snapshot)
    if disabled:
        return disabled[0]
    adapters = list((snapshot.get("bluetooth") or {}).get("adapters") or [])
    return adapters[0] if adapters else None


def enable_bluetooth_adapter(
    *,
    instance_id: str | None = None,
    elevate: bool = True,
    wait: bool = True,
) -> dict[str, Any]:
    """Opt-in repair when the Bluetooth radio is disabled (CM_PROB_DISABLED).

    Enables the PnP adapter and starts ``bthserv``, ``BTAGService``,
    ``BthAvctpSvc``, and ``DeviceAssociationService``. Restores **discovery
    capability**, not audio playback on an already-paired headset.

    Args:
        instance_id: PnP instance ID of the adapter; auto-detected when empty.
        elevate: Run the enable script elevated (UAC) when True.
        wait: Block until the elevated script finishes when True.

    Returns:
        Dict with ``elevated``, ``instance_id``, ``exit_code``, ``log``,
        ``reboot_required`` (always False), ``script_path``, and ``log_path``.
        Elevated mode uses the UAC wrapper exit code. Non-elevated mode reports
        ``exit_code=0`` whenever :func:`_run_powershell` returns without
        raising (script exit status is not preserved).

    Raises:
        RuntimeError: On non-Windows platforms.
    """
    if sys.platform != "win32":
        raise RuntimeError("Bluetooth adapter repair is available only on Windows.")

    safe_id = (instance_id or "").replace("'", "").replace('"', "")
    script = _BT_ENABLE_ADAPTER_SCRIPT.format(instance_id=safe_id)
    script_path = Path(tempfile.gettempdir()) / "wapc-bluetooth-enable.ps1"
    log_path = Path(tempfile.gettempdir()) / "wapc-bluetooth-enable.log"
    script_path.write_text(script, encoding="utf-8")
    if log_path.exists():
        log_path.unlink()

    if elevate:
        exit_code, log_text = _run_elevated_script(script_path, log_path, wait=wait)
        return {
            "elevated": True,
            "instance_id": safe_id or None,
            "exit_code": exit_code,
            "log": log_text,
            "reboot_required": False,
            "script_path": str(script_path),
            "log_path": str(log_path),
        }

    raw = _run_powershell(script, timeout=60)
    return {
        "elevated": False,
        "instance_id": safe_id or None,
        "exit_code": 0,
        "log": raw,
        "reboot_required": False,
        "script_path": str(script_path),
        "log_path": str(log_path),
    }


def repair_bluetooth_pairing(
    *,
    address: str,
    friendly_name: str,
    elevate: bool = True,
    wait: bool = True,
) -> dict[str, Any]:
    """Opt-in repair for stuck Bluetooth remove / Settings icon desync.

    Clears BTHPORT pairing registry keys for one **device address**. Does not
    remove PnP nodes (``Disable-PnpDevice`` / ``Remove-PnpDevice`` hang when
    Settings shows "Removing device"). After repair, user must reboot and
    re-pair; connected-in-Settings ≠ working audio until endpoints rebuild.

    Args:
        address: Target Bluetooth MAC (normalized internally).
        friendly_name: Log label only; not used for registry matching.
        elevate: Run the repair script elevated (UAC) when True.
        wait: Block until the elevated script finishes when True.

    Returns:
        Dict with ``elevated``, ``address``, ``friendly_name``, ``exit_code``,
        ``log``, ``reboot_required`` (True), ``script_path``, and ``log_path``.
        Elevated mode uses the UAC wrapper exit code. Non-elevated mode reports
        ``exit_code=0`` when :func:`_run_powershell` returns without raising.

    Raises:
        RuntimeError: On non-Windows platforms.
        ValueError: If ``address`` fails normalization.
    """
    if sys.platform != "win32":
        raise RuntimeError("Bluetooth repair is available only on Windows.")

    normalized = normalize_bluetooth_address(address)

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
        exit_code, log_text = _run_elevated_script(script_path, log_path, wait=wait)
        return {
            "elevated": True,
            "address": normalized,
            "friendly_name": friendly_name,
            "exit_code": exit_code,
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
