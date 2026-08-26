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
    PrivilegeCheck      = 'NOT_RUN'
    GhostCleanup        = 'NOT_RUN'
    AdapterReset        = 'NOT_RUN'
    ServicesHealthy     = 'NOT_RUN'
    DiscoveryApi        = 'NOT_RUN'
    TargetDiscovered    = 'NOT_RUN'
    ClassicEnumeration  = 'NOT_RUN'
    Pairability         = 'NOT_RUN'
    PairableEndpoint    = 'NOT_RUN'
    PairRequest         = 'NOT_RUN'
    PairingSucceeded    = 'NOT_RUN'
    AudioEndpoint       = 'NOT_RUN'
  }
}

function Set-WapcStageResult {
  param(
    [Parameter(Mandatory)]$Results,
    [Parameter(Mandatory)][string]$Stage,
    [Parameter(Mandatory)][string]$Value
  )
  if (-not $Results.Contains($Stage)) { return }
  $allowed = @('NOT_RUN', 'BLOCKED', 'SKIPPED')
  $current = [string]$Results[$Stage]
  if ($allowed -contains $current -or $current -eq $Value) {
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
    $Verification = $null
  )
  $report = [ordered]@{
    timestamp         = $Context.timestamp
    powershellVersion = $Context.powershell_version
    windowsBuild      = $Context.windows_build
    isElevated        = $Context.is_elevated
    targetName        = $Context.target_name
    targetAddress     = $Context.target_address
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
  ($Context.stages | ConvertTo-Json -Depth 4) | Set-Content -Path $Context.status_path -Encoding ASCII
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
  Write-Host 'Target'
  Write-Host $Context.target_name
  Write-Host $Context.target_address
  Write-Host ''
  $labels = [ordered]@{
    PrivilegeCheck     = 'Privilege'
    GhostCleanup       = 'Ghost cleanup'
    AdapterReset       = 'Adapter reset'
    ServicesHealthy    = 'Bluetooth services'
    DiscoveryApi       = 'Discovery API'
    TargetDiscovered   = 'Target discovered'
    ClassicEnumeration = 'Classic endpoint'
    Pairability        = 'Pairability'
    PairableEndpoint   = 'Pairable endpoint'
    PairRequest        = 'Pair request'
    PairingSucceeded   = 'Pairing succeeded'
    AudioEndpoint      = 'Audio endpoint'
  }
  foreach ($key in $labels.Keys) {
    Write-Host ('{0,-22} {1}' -f ($labels[$key] + ''), $Context.stages[$key])
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
      Write-Host ('Reason: {0}' -f $f.reason)
    }
  }
  Write-Host ''
  Write-Host ('Status JSON:      {0}' -f $Context.status_path)
  Write-Host ('Diagnostics JSON: {0}' -f $Context.diagnostics_path)
  Write-Host ('Log:              {0}' -f $Context.log_path)
}

function Get-WapcNamePatterns {
  param([string]$TargetName)
  $patterns = New-Object System.Collections.Generic.List[string]
  [void]$patterns.Add($TargetName)
  foreach ($part in ($TargetName -split '\s+')) {
    if ($part.Length -ge 4) { [void]$patterns.Add($part) }
  }
  return ,@($patterns.ToArray())
}

Export-ModuleMember -Function @(
  'Test-WapcElevation',
  'Restart-WapcElevated',
  'New-WapcStageResults',
  'Set-WapcStageResult',
  'New-WapcFailure',
  'New-WapcRecoveryContext',
  'Write-WapcLog',
  'Add-WapcError',
  'Write-WapcDiagnosticReport',
  'ConvertTo-WapcJsonArray',
  'Write-WapcFinalSummary',
  'Get-WapcNamePatterns'
)
