#Requires -Version 5.1
# WAPC Bluetooth recovery orchestrator (ASCII-only).
<#
.SYNOPSIS
  Ghost-pair cleanup, adapter/service recovery, ranked WinRT auto-pair.
.PARAMETER TargetName
  Bluetooth device friendly name to match.
.PARAMETER TargetAddress
  Bluetooth MAC address (hex, optional separators).
.PARAMETER Diagnostics
  Dump extended DeviceInformation properties and artifact JSON files.
.PARAMETER VerboseLog
  Log every discovered candidate each scan.
.PARAMETER NoCleanup
  Skip ghost-pair registry/PnP cleanup.
.PARAMETER NoAdapterReset
  Skip adapter disable/enable bounce.
.PARAMETER NoPair
  Discovery/ranking only; do not call PairAsync.
.PARAMETER DiscoveryOnly
  Run one discovery pass and exit ranking loop early.
.PARAMETER PairingTimeoutSec
  Reserved for future use (discovery window uses DiscoveryTimeoutSec).
.PARAMETER DiscoveryTimeoutSec
  Seconds to scan for pairable endpoints.
.PARAMETER WhatIf
  Show destructive cleanup actions without executing them.
.PARAMETER NoElevate
  Do not attempt self-elevation when not admin.
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
  [string]$TargetName = 'EDIFIER W800BT Pro',
  [string]$TargetAddress = 'c8247887e57c',
  [switch]$Diagnostics,
  [switch]$VerboseLog,
  [switch]$NoCleanup,
  [switch]$NoAdapterReset,
  [switch]$NoPair,
  [switch]$DiscoveryOnly,
  [int]$PairingTimeoutSec = 90,
  [int]$DiscoveryTimeoutSec = 90,
  [switch]$NoElevate
)

$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'

$scriptRoot = $PSScriptRoot
if (-not $scriptRoot) {
  $scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
}
$btModuleRoot = Join-Path $scriptRoot 'Bluetooth'

$requiredModules = @(
  (Join-Path $btModuleRoot 'WapcBluetoothCore.psm1'),
  (Join-Path $btModuleRoot 'WapcBluetoothIdentity.psm1'),
  (Join-Path $btModuleRoot 'WapcBluetoothCleanup.psm1'),
  (Join-Path $btModuleRoot 'WapcBluetoothServices.psm1'),
  (Join-Path $scriptRoot 'Platform\WinRT.psm1'),
  (Join-Path $btModuleRoot 'BluetoothPairingEngine.psm1')
)
foreach ($modPath in $requiredModules) {
  if (-not (Test-Path -LiteralPath $modPath)) {
    Write-Host ('FATAL: module missing: ' + $modPath)
    Write-Host 'Press any key to close...'
    $null = $Host.UI.RawUI.ReadKey('NoEcho,IncludeKeyDown')
    exit 2
  }
  # -Global keeps shared Core helpers visible after nested module imports.
  Import-Module -Name $modPath -Force -Global -ErrorAction Stop
}
Import-Module PnpDevice -ErrorAction SilentlyContinue

if (-not (Get-Command New-WapcRecoveryContext -ErrorAction SilentlyContinue)) {
  Write-Host 'FATAL: WapcBluetoothCore did not export New-WapcRecoveryContext.'
  Write-Host 'Press any key to close...'
  $null = $Host.UI.RawUI.ReadKey('NoEcho,IncludeKeyDown')
  exit 2
}

$Context = New-WapcRecoveryContext -TargetName $TargetName -TargetAddress $TargetAddress
('WAPC Bluetooth recovery {0}' -f (Get-Date)) | Set-Content -Path $Context.log_path -Encoding ASCII

function Log([string]$Message) { Write-WapcLog -Context $Context -Message $Message }

# --- PRIVILEGE CHECK ---
Set-WapcMachineState -Context $Context -State 'PRIVILEGE_CHECK' -Log ${function:Log}
Write-Host ''
Write-Host 'PRIVILEGE CHECK'
Write-Host '---------------'
if ($Context.is_elevated) {
  Write-Host 'Elevated token           PASS'
  Write-Host 'Registry cleanup         AVAILABLE'
  Write-Host 'PnP removal              AVAILABLE'
  Write-Host 'Service control          AVAILABLE'
  Write-Host 'Adapter control          AVAILABLE'
  Set-WapcStageResult -Results $Context.stages -Stage 'PrivilegeCheck' -Value 'PASS'
} else {
  Write-Host 'Elevated token           FAIL'
  Write-Host 'Registry cleanup         BLOCKED'
  Write-Host 'PnP removal              BLOCKED'
  Write-Host 'Service control          BLOCKED'
  Write-Host 'Adapter control          BLOCKED'
  Write-Host ''
  Write-Host 'Privileged recovery stages will not run.'
  Set-WapcStageResult -Results $Context.stages -Stage 'PrivilegeCheck' -Value 'FAIL'
  if (-not $NoElevate -and -not $DiscoveryOnly) {
    $argList = @(
      '-TargetName', $TargetName,
      '-TargetAddress', $TargetAddress,
      '-NoElevate'
    )
    if ($Diagnostics) { $argList += '-Diagnostics' }
    if ($VerboseLog) { $argList += '-VerboseLog' }
    if ($NoCleanup) { $argList += '-NoCleanup' }
    if ($NoAdapterReset) { $argList += '-NoAdapterReset' }
    if ($NoPair) { $argList += '-NoPair' }
    if ($DiscoveryOnly) { $argList += '-DiscoveryOnly' }
    $argList += @('-DiscoveryTimeoutSec', [string]$DiscoveryTimeoutSec)
    $env:WAPC_ELEVATED_RELAUNCH = '1'
    Log 'Attempting self-elevation...'
    Restart-WapcElevated -ScriptPath $MyInvocation.MyCommand.Path -ArgumentList $argList
    exit 1
  }
  $Context.failure_classification = 'INSUFFICIENT_PRIVILEGES'
  [void]$Context.failures.Add((New-WapcFailure -Stage 'PRIVILEGE_CHECK' `
    -Classification 'INSUFFICIENT_PRIVILEGES' `
    -Reason 'Run Fix-Edifier-Bluetooth.bat as Administrator' -Retryable $true))
  Set-WapcStageResult -Results $Context.stages -Stage 'GhostCleanup' -Value 'NOT_RUN'
  Set-WapcStageResult -Results $Context.stages -Stage 'AdapterReset' -Value 'NOT_RUN'
  Set-WapcStageResult -Results $Context.stages -Stage 'ServicesHealthy' -Value 'NOT_RUN'
  $Context.final_result = 'FAILED'
  Write-WapcDiagnosticReport -Context $Context
  Write-WapcFinalSummary -Context $Context
  Write-Host ''
  Write-Host 'Action:'
  Write-Host 'Run the tool from an elevated Administrator session.'
  Write-Host 'Press any key to close...'
  $null = $Host.UI.RawUI.ReadKey('NoEcho,IncludeKeyDown')
  exit 1
}

$adapterId = 'USB\VID_0489&PID_E14F&MI_00\8&9278AFE&0&0000'
$namePatterns = Get-WapcNamePatterns -TargetName $TargetName

try {
  # --- CLEANUP ---
  $cleanupVerified = $true
  if (-not $NoCleanup) {
    Set-WapcMachineState -Context $Context -State 'CLEANING_STALE_ASSOCIATION' -Log ${function:Log}
    $cleanup = Remove-WapcBluetoothGhostAssociation -Context $Context `
      -NamePatterns $namePatterns -DeviceAddress $TargetAddress `
      -WhatIfPreference:$WhatIfPreference -Log ${function:Log}
    if ($cleanup.postcondition -eq 'PASS' -and $cleanup.cleanup_verified) {
      Set-WapcStageResult -Results $Context.stages -Stage 'GhostCleanup' -Value 'PASS'
      $cleanupVerified = $true
    } elseif ($cleanup.postcondition -eq 'BLOCKED') {
      Set-WapcStageResult -Results $Context.stages -Stage 'GhostCleanup' -Value 'NOT_RUN'
      $cleanupVerified = $false
      $Context.failure_classification = 'INSUFFICIENT_PRIVILEGES'
      $Context.final_result = 'FAILED'
      [void]$Context.failures.Add((New-WapcFailure -Stage 'CLEANING_STALE_ASSOCIATION' `
        -Classification 'INSUFFICIENT_PRIVILEGES' -Reason 'Cleanup blocked without elevation'))
    } else {
      Set-WapcStageResult -Results $Context.stages -Stage 'GhostCleanup' -Value 'FAIL'
      $cleanupVerified = $false
      $Context.failure_classification = 'GHOST_CLEANUP_FAILED'
      $Context.final_result = 'FAILED'
      [void]$Context.failures.Add((New-WapcFailure -Stage 'CLEANING_STALE_ASSOCIATION' `
        -Classification 'GHOST_CLEANUP_FAILED' `
        -Reason ('Cleanup verification failed; remaining_nodes=' + $cleanup.remaining_nodes +
          ' pnp_removal=' + $cleanup.pnp_removal)))
    }
  } else {
    Set-WapcStageResult -Results $Context.stages -Stage 'GhostCleanup' -Value 'SKIPPED'
    $cleanupVerified = $true
  }

  if (-not $cleanupVerified) {
    Set-WapcMachineState -Context $Context -State 'CLEANUP_FAILED' -Log ${function:Log}
    Log 'Recovery aborted before adapter reset (cleanup not verified).'
    Set-WapcStageResult -Results $Context.stages -Stage 'AdapterReset' -Value 'NOT_RUN'
    Set-WapcStageResult -Results $Context.stages -Stage 'ServicesHealthy' -Value 'NOT_RUN'
    Set-WapcStageResult -Results $Context.stages -Stage 'DiscoveryApi' -Value 'NOT_RUN'
    Set-WapcStageResult -Results $Context.stages -Stage 'TargetDiscovered' -Value 'NOT_RUN'
    Set-WapcStageResult -Results $Context.stages -Stage 'TargetClassicEndpoint' -Value 'NOT_RUN'
    Set-WapcStageResult -Results $Context.stages -Stage 'PairRequest' -Value 'NOT_RUN'
    Set-WapcStageResult -Results $Context.stages -Stage 'PairResult' -Value 'NOT_RUN'
    Set-WapcStageResult -Results $Context.stages -Stage 'AudioEndpoint' -Value 'NOT_RUN'
  } else {

  # --- ADAPTER ---
  if (-not $NoAdapterReset) {
    Set-WapcMachineState -Context $Context -State 'RESETTING_ADAPTER' -Log ${function:Log}
    $adapter = Restart-WapcBluetoothAdapter -Context $Context -AdapterInstanceId $adapterId `
      -WhatIfPreference:$WhatIfPreference -Log ${function:Log}
    Set-WapcStageResult -Results $Context.stages -Stage 'AdapterReset' -Value $adapter.status
  } else {
    Set-WapcStageResult -Results $Context.stages -Stage 'AdapterReset' -Value 'SKIPPED'
  }

  # --- SERVICES ---
  Set-WapcMachineState -Context $Context -State 'CHECKING_SERVICES' -Log ${function:Log}
  $svc = Test-WapcBluetoothServices -Context $Context -Restart -WhatIfPreference:$WhatIfPreference -Log ${function:Log}
  Set-WapcStageResult -Results $Context.stages -Stage 'ServicesHealthy' -Value $svc.status

  Start-Process 'ms-settings:bluetooth' -ErrorAction SilentlyContinue
  Start-Sleep -Seconds 1

  # --- CAPABILITY ---
  Set-WapcMachineState -Context $Context -State 'CHECKING_DISCOVERY_CAPABILITY' -Log ${function:Log}
  Log '=== DISCOVERY CAPABILITY CHECK ==='
  $cap = Get-BluetoothDiscoveryCapability
  Write-BluetoothDiscoveryCapabilityReport -Capability $cap
  if ($cap.available) {
    Set-WapcStageResult -Results $Context.stages -Stage 'DiscoveryApi' -Value 'PASS'
  } else {
    Set-WapcStageResult -Results $Context.stages -Stage 'DiscoveryApi' -Value 'FAIL'
    $Context.failure_classification = 'DISCOVERY_API_UNAVAILABLE'
    [void]$Context.failures.Add((New-WapcFailure -Stage 'CHECKING_DISCOVERY_CAPABILITY' `
      -Classification 'DISCOVERY_API_UNAVAILABLE' -Reason ([string]$cap.reason)))
    Set-WapcStageResult -Results $Context.stages -Stage 'TargetDiscovered' -Value 'NOT_RUN'
    Set-WapcStageResult -Results $Context.stages -Stage 'ClassicEnumerationCapability' -Value 'NOT_RUN'
    Set-WapcStageResult -Results $Context.stages -Stage 'TargetClassicEndpoint' -Value 'NOT_RUN'
    Set-WapcStageResult -Results $Context.stages -Stage 'Pairability' -Value 'NOT_RUN'
    Set-WapcStageResult -Results $Context.stages -Stage 'PairableEndpoint' -Value 'NOT_RUN'
    Set-WapcStageResult -Results $Context.stages -Stage 'PairRequest' -Value 'NOT_RUN'
    Set-WapcStageResult -Results $Context.stages -Stage 'PairResult' -Value 'NOT_RUN'
    Set-WapcStageResult -Results $Context.stages -Stage 'AudioEndpoint' -Value 'NOT_RUN'
  }

  $pairOutcome = $null
  if ($cap.available) {
    $pairOutcome = Invoke-WapcBluetoothPairing -Context $Context `
      -DiscoveryTimeoutSec $DiscoveryTimeoutSec -Diagnostics:$Diagnostics `
      -VerboseLog:$VerboseLog -NoPair:$NoPair -DiscoveryOnly:$DiscoveryOnly -Log ${function:Log}
  }

  if ($pairOutcome -and $pairOutcome.classification -eq 'SUCCESS') {
    $Context.final_result = 'SUCCESS'
  } elseif ($Context.failure_classification) {
    $Context.final_result = 'FAILED'
  } elseif ($pairOutcome -and $pairOutcome.classification) {
    $Context.failure_classification = $pairOutcome.classification
    $Context.final_result = 'FAILED'
  } elseif ($pairOutcome -and $pairOutcome.pair_success -and $pairOutcome.verification -and -not $pairOutcome.verification.exact_target_audio_endpoint_found -and -not $pairOutcome.verification.audio_endpoint_ready) {
    $Context.final_result = 'PARTIAL_SUCCESS'
    if (-not $Context.failure_classification) {
      $Context.failure_classification = 'PAIRING_SUCCEEDED_AUDIO_ENDPOINT_MISSING'
    }
  } else {
    $Context.final_result = 'FAILED'
  }

  } # end cleanupVerified else - adapter/discovery/pairing only after cleanup PASS
} finally {
  if ($Context.is_elevated) {
    foreach ($s in @('bthserv', 'BTAGService', 'BthAvctpSvc', 'DeviceAssociationService')) {
      try { Start-Service $s -ErrorAction SilentlyContinue } catch { }
    }
    try {
      Enable-PnpDevice -InstanceId $adapterId -Confirm:$false -ErrorAction SilentlyContinue | Out-Null
    } catch { }
  }
}

Write-WapcDiagnosticReport -Context $Context
Write-WapcFinalSummary -Context $Context
Write-Host 'Press any key to close...'
$null = $Host.UI.RawUI.ReadKey('NoEcho,IncludeKeyDown')
$exitCode = 1
if ($Context.final_result -eq 'SUCCESS') {
  $exitCode = 0
} elseif ($Context.failure_classification -and (Get-Command Get-WapcExitCodeForClassification -ErrorAction SilentlyContinue)) {
  $exitCode = Get-WapcExitCodeForClassification -Classification $Context.failure_classification
}
exit $exitCode
