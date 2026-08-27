#Requires -Version 5.1
# Core utilities: elevation, stage results, failures, diagnostic reports (ASCII-only).

function Test-WapcElevation {
  $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
  $principal = New-Object Security.Principal.WindowsPrincipal($identity)
  return [bool]$principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Restart-WapcElevated {
  param(
    [Parameter(Mandatory)][string]$ScriptPath,
    [string[]]$ArgumentList = @()
  )
  if ($env:WAPC_ELEVATED_RELAUNCH -eq '1') {
    return $false
  }
  $escaped = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $ScriptPath) + $ArgumentList
  $argLine = ($escaped | ForEach-Object {
    if ($_ -match '\s') { '"' + $_ + '"' } else { $_ }
  }) -join ' '
  try {
    $proc = Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList $argLine -PassThru -Wait
    if ($proc) { exit $proc.ExitCode }
    exit 1
  } catch {
    return $false
  }
}

function New-WapcStageResults {
  return [ordered]@{
    PrivilegeCheck                 = 'NOT_RUN'
    GhostCleanup                   = 'NOT_RUN'
    AdapterReset                   = 'NOT_RUN'
    ServicesHealthy                = 'NOT_RUN'
    DiscoveryApi                   = 'NOT_RUN'
    ClassicEnumerationCapability   = 'NOT_RUN'
    TargetDiscovered               = 'NOT_RUN'
    TargetClassicEndpoint          = 'NOT_RUN'
    Pairability                    = 'NOT_RUN'
    PairableEndpoint               = 'NOT_RUN'
    PairRequest                    = 'NOT_RUN'
    PairResult                     = 'NOT_RUN'
    AudioEndpoint                  = 'NOT_RUN'
  }
}

function Get-WapcStageStatusKeyMap {
  return [ordered]@{
    PrivilegeCheck               = 'privilege'
    GhostCleanup                 = 'ghost_cleanup'
    AdapterReset                 = 'adapter_reset'
    ServicesHealthy              = 'bluetooth_services'
    DiscoveryApi                 = 'discovery_api'
    ClassicEnumerationCapability = 'classic_enumeration_api'
    TargetDiscovered             = 'configured_target_found'
    TargetClassicEndpoint        = 'target_classic_endpoint'
    Pairability                  = 'pairability'
    PairableEndpoint             = 'pairable_endpoint'
    PairRequest                  = 'pair_request'
    PairResult                   = 'pair_result'
    AudioEndpoint                = 'audio_endpoint'
  }
}

function ConvertTo-WapcStatusSnapshot {
  param(
    [Parameter(Mandatory)]$Context,
    $DiscoveryMeta = $null
  )
  $keyMap = Get-WapcStageStatusKeyMap
  $snapshot = [ordered]@{}
  foreach ($stageKey in $Context.stages.Keys) {
    $jsonKey = [string]$keyMap[$stageKey]
    if (-not $jsonKey) { $jsonKey = $stageKey }
    $snapshot[$jsonKey] = [string]$Context.stages[$stageKey]
  }
  $snapshot['overall_result'] = [string]$Context.final_result
  if ($Context.failure_classification) {
    $snapshot['classification'] = [string]$Context.failure_classification
  }
  if ($DiscoveryMeta) {
    foreach ($prop in @('cycles_attempted', 'target_seen', 'non_target_candidates_seen', 'elapsed_seconds')) {
      if ($null -ne $DiscoveryMeta[$prop]) {
        $snapshot[$prop] = $DiscoveryMeta[$prop]
      }
    }
  }
  if ($Context.observed_non_target_devices -and $Context.observed_non_target_devices.Count -gt 0) {
    $snapshot['observed_non_target_devices'] = @($Context.observed_non_target_devices)
  }
  return $snapshot
}

function Set-WapcDownstreamStagesNotRun {
  param(
    [Parameter(Mandatory)]$Results,
    [Parameter(Mandatory)][string[]]$FromStage
  )
  $order = @(
    'TargetClassicEndpoint', 'Pairability', 'PairableEndpoint',
    'PairRequest', 'PairResult', 'AudioEndpoint'
  )
  $start = [array]::IndexOf($order, $FromStage)
  if ($start -lt 0) { return }
  for ($i = $start; $i -lt $order.Count; $i++) {
    Set-WapcStageResult -Results $Results -Stage $order[$i] -Value 'NOT_RUN'
  }
}

function Repair-WapcStageResults {
  <#
  .SYNOPSIS
    Normalize impossible stage combinations before invariant validation.
    A stage that was never executed must not remain PASS/FAIL.
  #>
  param([Parameter(Mandatory)]$Results)

  $repaired = New-Object System.Collections.ArrayList
  $targetFound = [string]$Results.TargetDiscovered
  $pairRequest = [string]$Results.PairRequest
  $pairResult = [string]$Results.PairResult

  if ($targetFound -in @('FAIL', 'NOT_FOUND')) {
    foreach ($stage in @('TargetClassicEndpoint', 'Pairability', 'PairableEndpoint', 'PairRequest', 'PairResult', 'AudioEndpoint')) {
      if ($Results[$stage] -in @('PASS', 'FAIL', 'ERROR', 'UNKNOWN')) {
        Set-WapcStageResult -Results $Results -Stage $stage -Value 'NOT_RUN'
        [void]$repaired.Add("$stage->NOT_RUN(target_not_discovered)")
      }
    }
  }

  if ($pairRequest -in @('NOT_RUN', 'NOT_ATTEMPTED', 'NOT_REQUIRED', 'BLOCKED', 'SKIPPED')) {
    if ($Results.PairResult -in @('PASS', 'FAIL', 'ERROR')) {
      Set-WapcStageResult -Results $Results -Stage 'PairResult' -Value 'NOT_RUN'
      [void]$repaired.Add('PairResult->NOT_RUN(pair_request_not_executed)')
    }
  }

  if ($Results.PairResult -eq 'FAIL' -and $pairRequest -in @('NOT_RUN', 'NOT_ATTEMPTED')) {
    Set-WapcStageResult -Results $Results -Stage 'PairResult' -Value 'NOT_RUN'
    [void]$repaired.Add('PairResult->NOT_RUN(invalid_fail_without_request)')
  }

  return ,@($repaired.ToArray())
}

function Set-WapcStageResult {
  param(
    [Parameter(Mandatory)]$Results,
    [Parameter(Mandatory)][string]$Stage,
    [Parameter(Mandatory)][string]$Value
  )
  if (-not $Results.Contains($Stage)) { return }
  $allowed = @('NOT_RUN', 'BLOCKED', 'SKIPPED', 'NOT_FOUND', 'UNKNOWN')
  $current = [string]$Results[$Stage]
  # FAIL may override a premature PASS (invariant repair).
  if ($Value -eq 'FAIL' -or $allowed -contains $current -or $current -eq $Value) {
    $Results[$Stage] = $Value
  }
}

function New-WapcFailure {
  param(
    [Parameter(Mandatory)][string]$Stage,
    [Parameter(Mandatory)][string]$Classification,
    [Parameter(Mandatory)][string]$Reason,
    [bool]$Retryable = $true,
    [array]$Evidence = @()
  )
  return [ordered]@{
    stage          = $Stage
    classification = $Classification
    reason         = $Reason
    retryable      = $Retryable
    evidence       = @($Evidence)
  }
}

function New-WapcRecoveryContext {
  param(
    [string]$TargetName = 'EDIFIER W800BT Pro',
    [string]$TargetAddress = 'c8247887e57c',
    [string]$LogPath = $(Join-Path $env:TEMP 'wapc-bt-auto-pair.log'),
    [string]$StatusPath = $(Join-Path $env:TEMP 'wapc-bt-auto-pair-status.json'),
    [string]$DiagnosticsPath = $(Join-Path $env:TEMP 'wapc-bt-pair-diagnostics.json'),
    [string]$CandidatesPath = $(Join-Path $env:TEMP 'wapc-bt-candidates.json'),
    [string]$EnumerationPath = $(Join-Path $env:TEMP 'wapc-bt-enumeration.json')
  )
  return [ordered]@{
    target_name       = $TargetName
    target_address    = $TargetAddress
    log_path          = $LogPath
    status_path       = $StatusPath
    diagnostics_path  = $DiagnosticsPath
    candidates_path   = $CandidatesPath
    enumeration_path  = $EnumerationPath
    timestamp         = (Get-Date).ToUniversalTime().ToString('o')
    powershell_version = $PSVersionTable.PSVersion.ToString()
    windows_build     = [Environment]::OSVersion.Version.ToString()
    is_elevated       = (Test-WapcElevation)
    stages            = (New-WapcStageResults)
    machine_state     = 'INITIALIZING'
    errors            = New-Object System.Collections.ArrayList
    failures          = New-Object System.Collections.ArrayList
    failure_classification = $null
    final_result      = 'NOT_RUN'
    observed_non_target_devices = New-Object System.Collections.ArrayList
    discovery_meta    = $null
  }
}

function Write-WapcLog {
  param(
    [Parameter(Mandatory)]$Context,
    [Parameter(Mandatory)][string]$Message
  )
  $line = '{0:HH:mm:ss} {1}' -f (Get-Date), $Message
  Add-Content -Path $Context.log_path -Value $line -Encoding ASCII
  Write-Host $line
}

function Add-WapcError {
  param(
    [Parameter(Mandatory)]$Context,
    [Parameter(Mandatory)][string]$Stage,
    [Parameter(Mandatory)][string]$Message
  )
  [void]$Context.errors.Add([ordered]@{
    stage     = $Stage
    message   = $Message
    timestamp = (Get-Date).ToUniversalTime().ToString('o')
  })
}

function Write-WapcDiagnosticReport {
  param(
    [Parameter(Mandatory)]$Context,
    [array]$Candidates = @(),
    $Enumeration = $null,
    $Pairing = $null,
    $Verification = $null,
    $DiscoveryMeta = $null
  )
  if ($DiscoveryMeta) { $Context.discovery_meta = $DiscoveryMeta }
  $report = [ordered]@{
    schema_version    = 2
    timestamp         = $Context.timestamp
    powershellVersion = $Context.powershell_version
    windowsBuild      = $Context.windows_build
    isElevated        = $Context.is_elevated
    targetName        = $Context.target_name
    targetAddress     = $Context.target_address
    expected_identity = @{
      name    = $Context.target_name
      address = (($Context.target_address -replace '[^0-9a-fA-F]', '').ToLowerInvariant())
    }
    stages            = $Context.stages
    machineState      = $Context.machine_state
    candidates        = @($Candidates)
    enumeration       = $Enumeration
    pairing           = $Pairing
    verification      = $Verification
    errors            = @($Context.errors)
    failures          = @($Context.failures)
    failureClassification = $Context.failure_classification
    finalResult       = $Context.final_result
  }
  ($report | ConvertTo-Json -Depth 12) | Set-Content -Path $Context.diagnostics_path -Encoding ASCII
  $statusSnapshot = ConvertTo-WapcStatusSnapshot -Context $Context -DiscoveryMeta $(
    if ($DiscoveryMeta) { $DiscoveryMeta } else { $Context.discovery_meta }
  )
  ($statusSnapshot | ConvertTo-Json -Depth 6) | Set-Content -Path $Context.status_path -Encoding ASCII
  if ($Candidates.Count -gt 0) {
    (ConvertTo-WapcJsonArray -Items $Candidates -Depth 10) | Set-Content -Path $Context.candidates_path -Encoding ASCII
  }
  if ($null -ne $Enumeration) {
    ($Enumeration | ConvertTo-Json -Depth 10) | Set-Content -Path $Context.enumeration_path -Encoding ASCII
  }
}

function ConvertTo-WapcJsonArray {
  param(
    [array]$Items,
    [int]$Depth = 8
  )
  if (-not $Items -or $Items.Count -eq 0) { return '[]' }
  if ($Items.Count -eq 1) {
    return '[' + ($Items[0] | ConvertTo-Json -Depth $Depth -Compress) + ']'
  }
  return ($Items | ConvertTo-Json -Depth $Depth -Compress)
}

function Write-WapcFinalSummary {
  param([Parameter(Mandatory)]$Context)
  Write-Host ''
  Write-Host '========================================'
  Write-Host 'WAPC Bluetooth Recovery'
  Write-Host '========================================'
  Write-Host ''
  Write-Host 'Configured Target'
  Write-Host ('Name                       {0}' -f $Context.target_name)
  Write-Host ('Address                    {0}' -f $Context.target_address)
  if ($Context.observed_non_target_devices -and $Context.observed_non_target_devices.Count -gt 0) {
    Write-Host ''
    Write-Host 'Observed non-target device(s)'
    foreach ($obs in @($Context.observed_non_target_devices)) {
      Write-Host ('Name                       {0}' -f $obs.name)
      Write-Host ('Address                    {0}' -f $obs.address)
    }
  }
  Write-Host ''
  $labels = [ordered]@{
    PrivilegeCheck               = 'Privilege'
    GhostCleanup                 = 'Ghost cleanup'
    AdapterReset                 = 'Adapter reset'
    ServicesHealthy              = 'Bluetooth services'
    DiscoveryApi                 = 'Discovery API'
    ClassicEnumerationCapability = 'Classic enumeration API'
    TargetDiscovered             = 'Configured target found'
    TargetClassicEndpoint        = 'Target Classic endpoint'
    Pairability                  = 'Pairability'
    PairableEndpoint             = 'Pairable endpoint'
    PairRequest                  = 'Pair request'
    PairResult                   = 'Pair result'
    AudioEndpoint                = 'Audio endpoint'
  }
  foreach ($key in $labels.Keys) {
    Write-Host ('{0,-28} {1}' -f ($labels[$key] + ''), $Context.stages[$key])
  }
  if ($Context.discovery_meta) {
    Write-Host ''
    Write-Host 'Discovery window'
    $dm = $Context.discovery_meta
    if ($null -ne $dm.cycles_attempted) {
      Write-Host ('cycles_attempted           {0}' -f $dm.cycles_attempted)
    }
    if ($null -ne $dm.target_seen) {
      Write-Host ('target_seen                {0}' -f $dm.target_seen)
    }
    if ($null -ne $dm.non_target_candidates_seen) {
      Write-Host ('non_target_candidates_seen {0}' -f $dm.non_target_candidates_seen)
    }
    if ($null -ne $dm.elapsed_seconds) {
      Write-Host ('elapsed_seconds            {0}' -f $dm.elapsed_seconds)
    }
    if ($dm.non_target_candidates_seen -and -not $dm.target_seen) {
      Write-Host ''
      Write-Host 'Discovery infrastructure is healthy, but the configured target was not visible.'
    }
  }
  Write-Host ''
  Write-Host 'FINAL RESULT'
  Write-Host $Context.final_result
  if ($Context.failure_classification) {
    Write-Host ''
    Write-Host 'Classification:'
    Write-Host $Context.failure_classification
  }
  if ($Context.failures.Count -gt 0) {
    $f = $Context.failures[-1]
    if ($f.reason) {
      Write-Host ''
      Write-Host 'Reason:'
      Write-Host $f.reason
    }
  }
  if ($Context.observed_non_target_devices -and $Context.observed_non_target_devices.Count -gt 0) {
    Write-Host ''
    Write-Host 'No destructive action was performed against the non-target device(s).'
  }
  Write-Host ''
  Write-Host ('Status JSON:      {0}' -f $Context.status_path)
  Write-Host ('Diagnostics JSON: {0}' -f $Context.diagnostics_path)
  Write-Host ('Log:              {0}' -f $Context.log_path)
}

function Get-WapcNamePatterns {
  param([string]$TargetName)
  # Prefer full name + distinctive tokens (model codes with digits).
  # Never emit bare brand tokens like "EDIFIER" — they match sibling headsets.
  $patterns = New-Object System.Collections.Generic.List[string]
  if ($TargetName) { [void]$patterns.Add($TargetName) }
  foreach ($part in ($TargetName -split '\s+')) {
    if ($part.Length -lt 4) { continue }
    if ($part -notmatch '\d') { continue }
    [void]$patterns.Add($part)
  }
  if ($patterns.Count -eq 0 -and $TargetName) { [void]$patterns.Add($TargetName) }
  return ,@($patterns.ToArray())
}

Export-ModuleMember -Function @(
  'Test-WapcElevation',
  'Restart-WapcElevated',
  'New-WapcStageResults',
  'Set-WapcStageResult',
  'Get-WapcStageStatusKeyMap',
  'ConvertTo-WapcStatusSnapshot',
  'Set-WapcDownstreamStagesNotRun',
  'Repair-WapcStageResults',
  'New-WapcFailure',
  'New-WapcRecoveryContext',
  'Write-WapcLog',
  'Add-WapcError',
  'Write-WapcDiagnosticReport',
  'ConvertTo-WapcJsonArray',
  'Write-WapcFinalSummary',
  'Get-WapcNamePatterns'
)
