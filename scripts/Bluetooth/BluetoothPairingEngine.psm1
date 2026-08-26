#Requires -Version 5.1
# Bluetooth pairing engine: state machine, pairability, PairAsync, verification.

# -Global prevents nested-module steal of Core exports from the caller session.
Import-Module (Join-Path $PSScriptRoot 'WapcBluetoothCore.psm1') -Force -Global

function Set-WapcMachineState {
  param(
    [Parameter(Mandatory)]$Context,
    [Parameter(Mandatory)][string]$State,
    [int]$Cycle = 0,
    [scriptblock]$Log = { param($m) Write-Host $m }
  )
  $Context.machine_state = $State
  if ($Cycle -gt 0) {
    & $Log ('STATE ' + $State + ' cycle=' + $Cycle)
  } else {
    & $Log ('STATE ' + $State)
  }
}

function Map-WapcPairAsyncStatus {
  param([string]$Status)
  switch -Regex ($Status) {
    '^(Paired|AlreadyPaired)$' { return @{ normalized = 'PAIRED'; failure = $null } }
    'Authentication' { return @{ normalized = 'AUTHENTICATION_FAILURE'; failure = 'PAIR_AUTHENTICATION_FAILED' } }
    'Timeout' { return @{ normalized = 'PAIRING_TIMEOUT'; failure = 'PAIRING_TIMEOUT' } }
    'Rejected|NotPaired|Failed|ConnectionRejected' { return @{ normalized = 'PAIRING_REJECTED'; failure = 'PAIRING_REJECTED' } }
    'NotReadyToPair' { return @{ normalized = 'DEVICE_NOT_AVAILABLE'; failure = 'DISCOVERABLE_NOT_PAIRABLE' } }
    'OperationAlreadyInProgress' { return @{ normalized = 'PAIRING_API_ERROR'; failure = 'PAIRING_ALREADY_IN_PROGRESS' } }
    default { return @{ normalized = 'PAIRING_API_ERROR'; failure = 'PAIR_REQUEST_FAILED' } }
  }
}

function Write-WapcPairingDiagnosis {
  param(
    [Parameter(Mandatory)]$Context,
    $Enumeration,
    [string]$Pairability,
    [string]$Classification
  )
  Write-Host ''
  Write-Host '[PAIRING DIAGNOSIS]'
  Write-Host ''
  if ($Enumeration.target_discovered) {
    Write-Host 'Target visibility'
    Write-Host ('PASS - {0} was discovered.' -f $Context.target_name)
  } else {
    Write-Host 'Target visibility'
    Write-Host 'FAIL - target not discovered during scan window.'
  }
  Write-Host ''
  if ($Enumeration.classic_enumeration_all_failed) {
    Write-Host 'Windows Classic endpoint enumeration'
    Write-Host 'ERROR - typed WinRT FindAllAsync invocation failed.'
  } elseif ($Enumeration.classic_enumeration_succeeded) {
    Write-Host 'Windows Classic endpoint enumeration'
    Write-Host 'PASS'
  } else {
    Write-Host 'Windows Classic endpoint enumeration'
    Write-Host 'UNKNOWN'
  }
  Write-Host ''
  Write-Host ('Pairability: {0}' -f $Pairability)
  if ($Pairability -eq 'UNKNOWN') {
    Write-Host 'UNKNOWN - cannot determine until Classic endpoint enumeration succeeds.'
  }
  Write-Host ''
  if ($Classification -in @('CLASSIC_ENDPOINT_ENUMERATION_FAILED', 'PAIRABILITY_UNDETERMINED', 'INSUFFICIENT_PRIVILEGES')) {
    Write-Host 'Most likely cause:'
    Write-Host 'LOCAL_TOOLING_OR_PRIVILEGE_FAILURE'
    Write-Host ''
    Write-Host 'Confidence:'
    Write-Host 'HIGH'
  } elseif ($Classification -eq 'DISCOVERABLE_NOT_PAIRABLE') {
    Write-Host 'Most likely cause:'
    Write-Host 'HEADSET_PAIRING_MODE_OR_MULTIPOINT'
    Write-Host ''
    Write-Host 'Recommended checks:'
    Write-Host '1. Hold headset power until pairing LED flashes.'
    Write-Host '2. Disconnect EDIFIER from phones/tablets using multipoint.'
    Write-Host '3. Keep headset within 1 metre of the PC.'
  }
}

function Invoke-WapcBluetoothPairing {
  param(
    [Parameter(Mandatory)]$Context,
    [int]$PairingTimeoutSec = 90,
    [int]$DiscoveryTimeoutSec = 90,
    [switch]$Diagnostics,
    [switch]$VerboseLog,
    [switch]$NoPair,
    [switch]$DiscoveryOnly,
    [scriptblock]$Log = { param($m) Write-Host $m }
  )

  $btRoot = $PSScriptRoot
  Import-Module (Join-Path $btRoot 'BluetoothDiscovery.psm1') -Force
  Import-Module (Join-Path $btRoot 'BluetoothCandidateRanker.psm1') -Force
  Import-Module (Join-Path $btRoot 'BluetoothPairingVerifier.psm1') -Force

  $namePatterns = Get-WapcNamePatterns -TargetName $Context.target_name
  $pairResultType = [Windows.Devices.Enumeration.DevicePairingResult]
  Initialize-WapcWinRtBluetoothTypes

  Set-WapcMachineState -Context $Context -State 'WAITING_FOR_PAIRING_MODE' -Log $Log
  & $Log 'Put target device in pairing mode NOW (LED flashing).'

  $deadline = (Get-Date).AddSeconds($DiscoveryTimeoutSec)
  $cycle = 0
  $allCandidates = New-Object System.Collections.ArrayList
  $lastEnumeration = $null
  $lastRank = $null
  $pairSuccess = $false
  $pairAttempted = $false
  $selectedCandidate = $null
  $pairingResult = $null
  $verification = $null
  $failure = $null

  while (((Get-Date) -lt $deadline) -and (-not $pairSuccess)) {
    $cycle++
    Set-WapcMachineState -Context $Context -State 'DISCOVERING' -Cycle $cycle -Log $Log

    $disc = Get-WapcBluetoothCandidates -NamePatterns $namePatterns `
      -ExpectedAddress $Context.target_address -Diagnostics:$Diagnostics -Log $Log
    $lastEnumeration = $disc.enumeration
    $batch = @($disc.candidates)

    foreach ($c in $batch) {
      $exists = $false
      foreach ($e in $allCandidates) { if ($e.id -eq $c.id) { $exists = $true; break } }
      if (-not $exists) { [void]$allCandidates.Add($c) }
    }

    if ($lastEnumeration.target_discovered) {
      Set-WapcStageResult -Results $Context.stages -Stage 'TargetDiscovered' -Value 'PASS'
    }

    if ($lastEnumeration.classic_enumeration_all_failed) {
      Set-WapcStageResult -Results $Context.stages -Stage 'ClassicEnumeration' -Value 'ERROR'
    } elseif ($lastEnumeration.classic_enumeration_succeeded) {
      Set-WapcStageResult -Results $Context.stages -Stage 'ClassicEnumeration' -Value 'PASS'
    }

    if ($VerboseLog -or $Diagnostics) {
      for ($i = 0; $i -lt $batch.Count; $i++) {
        Write-BluetoothCandidateLog -Candidates $batch -Index $i -VerboseLog:$VerboseLog
      }
    }

    if ($batch.Count -eq 0) {
      & $Log 'No target candidates this scan - keep pairing mode active'
      Start-Sleep -Seconds 5
      continue
    }

    Set-WapcMachineState -Context $Context -State 'RANKING_CANDIDATES' -Cycle $cycle -Log $Log
    $classicOk = [bool]$lastEnumeration.classic_enumeration_succeeded
    $aepOk = [bool]$lastEnumeration.aep_enumeration_succeeded
    $lastRank = Rank-WapcBluetoothCandidates -Candidates @($allCandidates.ToArray()) `
      -TargetName $Context.target_name -TargetAddress $Context.target_address `
      -ClassicEnumOk:$classicOk -AepEnumOk:$aepOk -Log $Log
    Write-BluetoothCandidateRanking -RankResult $lastRank

    $pairability = [string]$lastRank.pairability
    if ($pairability -eq 'UNKNOWN') {
      Set-WapcStageResult -Results $Context.stages -Stage 'Pairability' -Value 'UNKNOWN'
    } elseif ($pairability -eq 'PAIRABLE') {
      Set-WapcStageResult -Results $Context.stages -Stage 'Pairability' -Value 'PASS'
    } else {
      Set-WapcStageResult -Results $Context.stages -Stage 'Pairability' -Value 'FAIL'
    }

    if ($DiscoveryOnly) { break }

    if ($NoPair) { Start-Sleep -Seconds 5; continue }

    if ($pairability -eq 'UNKNOWN') {
      & $Log 'Pairability UNKNOWN due to enumeration errors - retrying scan'
      Start-Sleep -Seconds 5
      continue
    }

    $selected = $lastRank.selected
    if ($selected -and $selected.can_pair -and -not $selected.is_paired) {
      Set-WapcStageResult -Results $Context.stages -Stage 'PairableEndpoint' -Value 'PASS'
      Set-WapcMachineState -Context $Context -State 'PAIRABLE_CANDIDATE_FOUND' -Log $Log

      $devRef = $null
      foreach ($c in $allCandidates) { if ($c.id -eq $selected.id) { $devRef = $c.device_ref; break } }
      if (-not $devRef) {
        & $Log 'Selected candidate missing device_ref - rescanning'
        Start-Sleep -Seconds 3
        continue
      }

      Set-WapcMachineState -Context $Context -State 'PAIRING' -Log $Log
      Set-WapcStageResult -Results $Context.stages -Stage 'PairRequest' -Value 'PASS'
      $pairAttempted = $true
      $selectedCandidate = $selected

      $pop = $devRef.Pairing.PairAsync()
      $pres = Invoke-WapcWinRt $pop $pairResultType 60000
      if ($pres) {
        $mapped = Map-WapcPairAsyncStatus -Status ([string]$pres.Status)
        $pairingResult = @{
          status = [string]$pres.Status
          normalized = $mapped.normalized
          protection = [string]$pres.ProtectionLevelUsed
        }
        & $Log ('PairAsync result: Status=' + $pres.Status + ' normalized=' + $mapped.normalized)
        if ($mapped.normalized -match 'PAIRED') {
          $pairSuccess = $true
          Set-WapcStageResult -Results $Context.stages -Stage 'PairingSucceeded' -Value 'PASS'
          break
        }
        $failure = New-WapcFailure -Stage 'PAIRING' -Classification $mapped.failure `
          -Reason ('PairAsync returned ' + $pres.Status)
        break
      } else {
        $failure = New-WapcFailure -Stage 'PAIRING' -Classification 'PAIRING_TIMEOUT' -Reason 'PairAsync timed out'
        break
      }
    } elseif ($selected -and $selected.is_paired) {
      $pairSuccess = $true
      $selectedCandidate = $selected
      Set-WapcStageResult -Results $Context.stages -Stage 'PairableEndpoint' -Value 'PASS'
      Set-WapcStageResult -Results $Context.stages -Stage 'PairingSucceeded' -Value 'PASS'
      break
    } else {
      Write-Host ''
      Write-Host ('[DISCOVERY] {0} detected' -f $Context.target_name)
      Write-Host ('Endpoints this scan: {0}' -f $batch.Count)
      Write-Host '[ACTION] Searching for pairable Classic association endpoint...'
      Start-Sleep -Seconds 5
    }
  }

  # Final classification
  if (-not $lastEnumeration) {
    $lastEnumeration = [ordered]@{
      target_discovered = $false
      classic_enumeration_succeeded = $false
      classic_enumeration_all_failed = $true
      aep_enumeration_succeeded = $false
      aep_enumeration_all_failed = $true
    }
  }

  if (-not $lastEnumeration.target_discovered) {
    Set-WapcStageResult -Results $Context.stages -Stage 'TargetDiscovered' -Value 'FAIL'
    if (-not $failure) {
      $failure = New-WapcFailure -Stage 'DISCOVERING' -Classification 'TARGET_NOT_DISCOVERED' `
        -Reason 'Target not seen during discovery window'
    }
  }

  if ($lastEnumeration.classic_enumeration_all_failed -and -not $pairSuccess) {
    if (-not $failure) {
      $failure = New-WapcFailure -Stage 'ENUMERATING_CLASSIC_ENDPOINTS' `
        -Classification 'CLASSIC_ENDPOINT_ENUMERATION_FAILED' `
        -Reason 'All Classic/AEP WinRT enumerations failed'
    }
    Set-WapcStageResult -Results $Context.stages -Stage 'ClassicEnumeration' -Value 'ERROR'
    Set-WapcStageResult -Results $Context.stages -Stage 'Pairability' -Value 'UNKNOWN'
    Set-WapcStageResult -Results $Context.stages -Stage 'PairRequest' -Value 'NOT_RUN'
    Set-WapcStageResult -Results $Context.stages -Stage 'PairingSucceeded' -Value 'NOT_RUN'
  } elseif ($lastRank -and $lastRank.pairability -eq 'UNKNOWN' -and -not $pairSuccess) {
    if (-not $failure) {
      $failure = New-WapcFailure -Stage 'RANKING_CANDIDATES' -Classification 'PAIRABILITY_UNDETERMINED' `
        -Reason 'Enumeration incomplete; pairability cannot be determined'
    }
    Set-WapcStageResult -Results $Context.stages -Stage 'PairRequest' -Value 'NOT_RUN'
    Set-WapcStageResult -Results $Context.stages -Stage 'PairingSucceeded' -Value 'NOT_RUN'
  } elseif ($lastRank -and $lastRank.pairability -eq 'NOT_PAIRABLE' -and -not $pairSuccess -and -not $DiscoveryOnly) {
    if (-not $failure) {
      $failure = New-WapcFailure -Stage 'RANKING_CANDIDATES' -Classification 'DISCOVERABLE_NOT_PAIRABLE' `
        -Reason 'Target visible but no pairable Classic endpoint found'
    }
    Set-WapcStageResult -Results $Context.stages -Stage 'PairableEndpoint' -Value 'FAIL'
    Set-WapcStageResult -Results $Context.stages -Stage 'PairRequest' -Value 'NOT_RUN'
    Set-WapcStageResult -Results $Context.stages -Stage 'PairingSucceeded' -Value 'NOT_RUN'
  }

  if ($pairSuccess -and -not $NoPair) {
    Set-WapcMachineState -Context $Context -State 'VERIFYING_AUDIO_PATH' -Log $Log
    $verification = Test-BluetoothPairVerification -Log $Log -WaitSeconds 30 `
      -NamePatterns ($namePatterns | ForEach-Object { $_ })
    if ($verification.audio_endpoint_ready) {
      Set-WapcStageResult -Results $Context.stages -Stage 'AudioEndpoint' -Value 'PASS'
    } else {
      Set-WapcStageResult -Results $Context.stages -Stage 'AudioEndpoint' -Value 'FAIL'
      if (-not $failure) {
        $failure = New-WapcFailure -Stage 'VERIFYING_AUDIO_PATH' `
          -Classification 'PAIRING_SUCCEEDED_AUDIO_ENDPOINT_MISSING' `
          -Reason 'Pair succeeded but audio endpoint did not appear'
      }
    }
  } elseif (-not $pairAttempted) {
    Set-WapcStageResult -Results $Context.stages -Stage 'AudioEndpoint' -Value 'NOT_RUN'
  }

  $classification = if ($failure) { $failure.classification } elseif ($pairSuccess -and $verification -and $verification.audio_endpoint_ready) { 'SUCCESS' } else { $null }
  if ($classification) { $Context.failure_classification = $classification }
  if ($failure) { [void]$Context.failures.Add($failure) }

  Write-WapcPairingDiagnosis -Context $Context -Enumeration $lastEnumeration `
    -Pairability $(if ($lastRank) { $lastRank.pairability } else { 'UNKNOWN' }) `
    -Classification $(if ($classification) { $classification } else { '' })

  Write-WapcDiagnosticReport -Context $Context -Candidates @($allCandidates.ToArray()) `
    -Enumeration $lastEnumeration -Pairing $pairingResult -Verification $verification

  return [pscustomobject]@{
    pair_success = $pairSuccess
    pair_attempted = $pairAttempted
    selected_candidate = $selectedCandidate
    enumeration = $lastEnumeration
    rank = $lastRank
    verification = $verification
    failure = $failure
    classification = $classification
  }
}

Export-ModuleMember -Function @(
  'Invoke-WapcBluetoothPairing',
  'Set-WapcMachineState',
  'Write-WapcPairingDiagnosis'
)

Set-Alias -Name Invoke-BluetoothAutoPair -Value Invoke-WapcBluetoothPairing -Scope Local
