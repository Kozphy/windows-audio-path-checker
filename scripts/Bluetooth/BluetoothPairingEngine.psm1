#Requires -Version 5.1
# Bluetooth pairing engine: state machine, pairability, PairAsync, verification.

# -Global prevents nested-module steal of Core exports from the caller session.
Import-Module (Join-Path $PSScriptRoot 'WapcBluetoothCore.psm1') -Force -Global
Import-Module (Join-Path $PSScriptRoot 'WapcBluetoothIdentity.psm1') -Force -Global

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
    [string]$Classification,
    [array]$ObservedCandidates = @(),
    [bool]$ExactTargetDiscovered = $false
  )
  Write-Host ''
  Write-Host '[PAIRING DIAGNOSIS]'
  Write-Host ''
  Write-Host 'TARGET'
  Write-Host ('  Name       {0}' -f $Context.target_name)
  Write-Host ('  Address    {0}' -f $Context.target_address)
  Write-Host ''
  Write-Host 'Target visibility'
  if ($ExactTargetDiscovered -or ($Enumeration -and $Enumeration.exact_target_discovered)) {
    Write-Host ('PASS - {0} was discovered (identity verified).' -f $Context.target_name)
  } else {
    Write-Host ('FAIL - {0} was not discovered.' -f $Context.target_name)
    Write-Host ''
    Write-Host 'Expected:'
    Write-Host ('  Name: {0}' -f $Context.target_name)
    Write-Host ('  Address: {0}' -f $Context.target_address)
    $others = @($ObservedCandidates | Where-Object {
      $_.disposition -ne 'ACCEPTED' -or -not $_.identity_matched
    })
    if ($others.Count -gt 0) {
      Write-Host ''
      Write-Host 'Other Bluetooth devices observed:'
      foreach ($o in $others) {
        Write-Host ('  {0}' -f $o.name)
        Write-Host ('  {0}' -f $o.device_address)
        Write-Host ('  Identity: MISMATCH ({0})' -f $(if ($o.rejection_reason) { $o.rejection_reason } else { 'REJECTED' }))
      }
    }
  }
  Write-Host ''
  if ($Enumeration.classic_enumeration_all_failed) {
    Write-Host 'Classic enumeration API'
    Write-Host 'ERROR - typed WinRT FindAllAsync invocation failed.'
  } elseif ($Enumeration.classic_enumeration_succeeded) {
    Write-Host 'Classic enumeration API'
    Write-Host 'PASS'
  } else {
    Write-Host 'Classic enumeration API'
    Write-Host 'UNKNOWN'
  }
  Write-Host ''
  if ($ExactTargetDiscovered) {
    Write-Host 'Target Classic endpoint'
    Write-Host 'FOUND'
  } else {
    Write-Host 'Target Classic endpoint'
    Write-Host 'NOT_FOUND'
  }
  Write-Host ''
  Write-Host ('Pairability: {0}' -f $Pairability)
  if ($Pairability -eq 'UNKNOWN') {
    if ($ExactTargetDiscovered) {
      Write-Host 'UNKNOWN - cannot determine pairability for the configured target.'
    } elseif ($Enumeration -and $Enumeration.classic_enumeration_succeeded) {
      Write-Host 'NOT_RUN - configured target Classic endpoint was not discovered.'
    } else {
      Write-Host 'UNKNOWN - Classic enumeration API did not succeed.'
    }
  } elseif ($Pairability -eq 'NOT_RUN') {
    Write-Host 'NOT_RUN - downstream stage skipped because configured target was not discovered.'
  }
  Write-Host ''
  if ($Classification -in @('CLASSIC_ENDPOINT_ENUMERATION_FAILED', 'PAIRABILITY_UNDETERMINED', 'INSUFFICIENT_PRIVILEGES')) {
    Write-Host 'Most likely cause:'
    Write-Host 'LOCAL_TOOLING_OR_PRIVILEGE_FAILURE'
    Write-Host ''
    Write-Host 'Confidence:'
    Write-Host 'HIGH'
  } elseif ($Classification -in @('TARGET_NOT_DISCOVERED', 'TARGET_IDENTITY_MISMATCH')) {
    Write-Host 'Most likely cause:'
    Write-Host 'TARGET_ABSENT_OR_WRONG_DEVICE_OBSERVED'
    Write-Host ''
    Write-Host 'Unrelated Bluetooth devices were detected but were not'
    Write-Host 'used as evidence for target recovery.'
  } elseif ($Classification -eq 'DISCOVERABLE_NOT_PAIRABLE') {
    Write-Host 'Most likely cause:'
    Write-Host 'HEADSET_PAIRING_MODE_OR_MULTIPOINT'
    Write-Host ''
    Write-Host 'Recommended checks:'
    Write-Host '1. Hold headset power until pairing LED flashes.'
    Write-Host '2. Disconnect target headset from phones/tablets using multipoint.'
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
  if (-not (Get-Command Get-WapcBluetoothCandidates -ErrorAction SilentlyContinue)) {
    Import-Module (Join-Path $btRoot 'BluetoothDiscovery.psm1') -Force -Global
  }
  if (-not (Get-Command Rank-WapcBluetoothCandidates -ErrorAction SilentlyContinue)) {
    Import-Module (Join-Path $btRoot 'BluetoothCandidateRanker.psm1') -Force -Global
  }
  if (-not (Get-Command Test-BluetoothPairVerification -ErrorAction SilentlyContinue)) {
    Import-Module (Join-Path $btRoot 'BluetoothPairingVerifier.psm1') -Force -Global
  }

  $namePatterns = Get-WapcNamePatterns -TargetName $Context.target_name
  $pairResultType = [Windows.Devices.Enumeration.DevicePairingResult]
  Initialize-WapcWinRtBluetoothTypes

  Set-WapcMachineState -Context $Context -State 'WAITING_FOR_PAIRING_MODE' -Log $Log
  & $Log 'Put target device in pairing mode NOW (LED flashing).'

  $deadline = (Get-Date).AddSeconds($DiscoveryTimeoutSec)
  $discoveryStarted = Get-Date
  $cycle = 0
  $allCandidates = New-Object System.Collections.ArrayList
  $lastEnumeration = $null
  $lastRank = $null
  $pairSuccess = $false
  $pairAttempted = $false
  $exactAlreadyPaired = $false
  $selectedCandidate = $null
  $pairingResult = $null
  $verification = $null
  $failure = $null
  $pairRequestOutcome = 'NOT_RUN'
  $targetSeenInWindow = $false
  $nonTargetSeenInWindow = $false
  $rankerRan = $false

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

    if ($lastEnumeration.classic_enumeration_all_failed) {
      Set-WapcStageResult -Results $Context.stages -Stage 'ClassicEnumerationCapability' -Value 'ERROR'
    } elseif ($lastEnumeration.classic_enumeration_succeeded) {
      Set-WapcStageResult -Results $Context.stages -Stage 'ClassicEnumerationCapability' -Value 'PASS'
    }

    foreach ($c in @($batch)) {
      if ($c.disposition -eq 'ACCEPTED' -and $c.identity_matched) {
        $targetSeenInWindow = $true
      } elseif ($c.disposition -in @('REJECTED_WRONG_DEVICE', 'REJECTED_INSUFFICIENT_IDENTITY')) {
        $nonTargetSeenInWindow = $true
        $existsObs = $false
        foreach ($o in @($Context.observed_non_target_devices)) {
          if ($o.address -eq $c.device_address) { $existsObs = $true; break }
        }
        if (-not $existsObs -and $c.device_address) {
          [void]$Context.observed_non_target_devices.Add([ordered]@{
            name    = [string]$c.name
            address = [string]$c.device_address
            role    = 'NON_TARGET_DEVICE'
            reason  = 'CONFIGURED_TARGET_MISMATCH'
          })
        }
      }
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
    $rankerRan = $true
    $classicOk = [bool]$lastEnumeration.classic_enumeration_succeeded
    $aepOk = [bool]$lastEnumeration.aep_enumeration_succeeded
    $lastRank = Rank-WapcBluetoothCandidates -Candidates @($allCandidates.ToArray()) `
      -TargetName $Context.target_name -TargetAddress $Context.target_address `
      -ClassicEnumOk:$classicOk -AepEnumOk:$aepOk -Log $Log
    Write-BluetoothCandidateRanking -RankResult $lastRank

    if ($lastRank.exact_target_discovered -or $lastRank.target_discovered) {
      Set-WapcStageResult -Results $Context.stages -Stage 'TargetDiscovered' -Value 'PASS'
      if ($lastRank.selected -and ($lastRank.selected.is_classic -or $lastRank.selected.protocol_id)) {
        Set-WapcStageResult -Results $Context.stages -Stage 'TargetClassicEndpoint' -Value 'PASS'
      } elseif ($lastRank.exact_target_already_paired) {
        Set-WapcStageResult -Results $Context.stages -Stage 'TargetClassicEndpoint' -Value 'PASS'
      } else {
        Set-WapcStageResult -Results $Context.stages -Stage 'TargetClassicEndpoint' -Value 'NOT_FOUND'
      }
      if ($lastEnumeration) {
        $lastEnumeration.target_discovered = $true
        $lastEnumeration.exact_target_discovered = $true
      }
    } else {
      Set-WapcStageResult -Results $Context.stages -Stage 'TargetDiscovered' -Value 'FAIL'
      Set-WapcStageResult -Results $Context.stages -Stage 'TargetClassicEndpoint' -Value 'NOT_RUN'
      if ($lastEnumeration) {
        $lastEnumeration.target_discovered = $false
        $lastEnumeration.exact_target_discovered = $false
      }
    }

    $pairability = [string]$lastRank.pairability
    if (-not ($lastRank.exact_target_discovered -or $lastRank.target_discovered)) {
      Set-WapcStageResult -Results $Context.stages -Stage 'Pairability' -Value 'NOT_RUN'
    } elseif ($pairability -eq 'UNKNOWN') {
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
    # Hard identity gate — never pair/verify a wrong-address device.
    if ($selected -and -not $selected.identity_matched -and $selected.disposition -ne 'ACCEPTED') {
      & $Log ('Rejecting selected candidate as wrong device: ' + $selected.name + ' ' + $selected.device_address)
      $selected = $null
    }

    if ($selected -and $selected.can_pair -and -not $selected.is_paired -and $selected.identity_matched) {
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
      $pairRequestOutcome = 'PAIR_REQUEST_STARTED'
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
          $pairRequestOutcome = 'PAIR_REQUEST_SUCCEEDED'
          Set-WapcStageResult -Results $Context.stages -Stage 'PairResult' -Value 'PASS'
          break
        }
        $pairRequestOutcome = 'PAIR_REQUEST_FAILED'
        $failure = New-WapcFailure -Stage 'PAIRING' -Classification $mapped.failure `
          -Reason ('PairAsync returned ' + $pres.Status)
        break
      } else {
        $pairRequestOutcome = 'PAIR_REQUEST_TIMEOUT'
        $failure = New-WapcFailure -Stage 'PAIRING' -Classification 'PAIRING_TIMEOUT' -Reason 'PairAsync timed out'
        break
      }
    } elseif ($selected -and $selected.is_paired -and $selected.identity_matched) {
      # Exact target already paired — verify *target* A2DP/audio, never sibling endpoints.
      $exactAlreadyPaired = $true
      $quick = Test-BluetoothPairVerification -NamePatterns $namePatterns `
        -DeviceAddress $Context.target_address -TargetName $Context.target_name `
        -WaitSeconds 4 -Log $Log
      if ($quick.exact_target_audio_endpoint_found -and $quick.exact_target_a2dp_endpoint_found) {
        $pairSuccess = $true
        $selectedCandidate = $selected
        Set-WapcStageResult -Results $Context.stages -Stage 'PairableEndpoint' -Value 'PASS'
        Set-WapcStageResult -Results $Context.stages -Stage 'PairRequest' -Value 'NOT_REQUIRED'
        Set-WapcStageResult -Results $Context.stages -Stage 'PairResult' -Value 'PASS'
        $verification = $quick
        break
      }

      & $Log 'Exact target IsPaired=True but target audio path missing - UnpairAsync then re-pair'
      $devRef = $null
      foreach ($c in $allCandidates) { if ($c.id -eq $selected.id) { $devRef = $c.device_ref; break } }
      if (-not $devRef) {
        $devRef = ($allCandidates | Where-Object { $_.device_address -eq $Context.target_address } | Select-Object -First 1).device_ref
      }
      if ($devRef) {
        try {
          $unpairType = [Windows.Devices.Enumeration.DeviceUnpairingResult]
          $null = [Windows.Devices.Enumeration.DeviceUnpairingResult, Windows.Devices.Enumeration, ContentType = WindowsRuntime]
          $uop = $devRef.Pairing.UnpairAsync()
          $ures = Invoke-WapcWinRt $uop $unpairType 30000
          & $Log ('UnpairAsync status=' + $(if ($ures) { $ures.Status } else { 'timeout/null' }))
        } catch {
          & $Log ('UnpairAsync failed: ' + $_.Exception.Message)
        }
      }
      $allCandidates = New-Object System.Collections.ArrayList
      & $Log 'Put headset in pairing mode (LED flashing) for re-pair...'
      Start-Sleep -Seconds 5
      continue
    } else {
      Write-Host ''
      if ($lastRank -and -not $lastRank.exact_target_discovered -and $batch.Count -gt 0) {
        Write-Host '[DISCOVERY] Bluetooth devices observed but none match target identity'
        foreach ($c in $batch) {
          Write-Host ('  observed={0} addr={1} disposition={2}' -f $c.name, $c.device_address, $c.disposition)
        }
      } else {
        Write-Host ('[DISCOVERY] {0} scan cycle' -f $Context.target_name)
        Write-Host ('Endpoints this scan: {0}' -f $batch.Count)
        Write-Host '[ACTION] Searching for pairable Classic association endpoint...'
      }
      Start-Sleep -Seconds 5
    }
  }

  $discoveryMeta = [ordered]@{
    cycles_attempted            = $cycle
    target_seen                 = [bool]$targetSeenInWindow
    non_target_candidates_seen  = [bool]$nonTargetSeenInWindow
    elapsed_seconds             = [math]::Round(((Get-Date) - $discoveryStarted).TotalSeconds, 1)
  }
  $Context.discovery_meta = $discoveryMeta

  # Final classification
  if (-not $lastEnumeration) {
    $lastEnumeration = [ordered]@{
      target_discovered = $false
      exact_target_discovered = $false
      any_bluetooth_device_discovered = $false
      classic_enumeration_succeeded = $false
      classic_enumeration_all_failed = $true
      aep_enumeration_succeeded = $false
      aep_enumeration_all_failed = $true
    }
  }

  $exactTargetDiscovered = [bool](
    $lastEnumeration.exact_target_discovered -or
    ($lastRank -and ($lastRank.exact_target_discovered -or $lastRank.target_discovered))
  )
  $identityMismatchObserved = [bool](
    $nonTargetSeenInWindow -and -not $exactTargetDiscovered
  )

  if (-not $exactTargetDiscovered) {
    Set-WapcStageResult -Results $Context.stages -Stage 'TargetDiscovered' -Value 'FAIL'
    Set-WapcDownstreamStagesNotRun -Results $Context.stages -FromStage 'TargetClassicEndpoint'
    if (-not $failure) {
      $cls = if ($identityMismatchObserved) { 'TARGET_IDENTITY_MISMATCH' } else { 'TARGET_NOT_DISCOVERED' }
      $reason = if ($identityMismatchObserved) {
        ('Configured target {0} ({1}) was not discovered. A different device was observed.' -f `
          $Context.target_name, $Context.target_address)
      } else {
        ('Configured target {0} ({1}) was not visible during the discovery window.' -f `
          $Context.target_name, $Context.target_address)
      }
      if ($nonTargetSeenInWindow) {
        $reason += ' Discovery infrastructure is healthy, but the configured target was not visible.'
      }
      $failure = New-WapcFailure -Stage 'DISCOVERING' -Classification $cls -Reason $reason
    }
  } elseif (-not $rankerRan) {
    Set-WapcDownstreamStagesNotRun -Results $Context.stages -FromStage 'TargetClassicEndpoint'
  }

  if ($lastEnumeration.classic_enumeration_all_failed -and -not $pairSuccess) {
    if (-not $failure) {
      $failure = New-WapcFailure -Stage 'ENUMERATING_CLASSIC_ENDPOINTS' `
        -Classification 'CLASSIC_ENDPOINT_ENUMERATION_FAILED' `
        -Reason 'All Classic/AEP WinRT enumerations failed'
    }
    Set-WapcStageResult -Results $Context.stages -Stage 'ClassicEnumerationCapability' -Value 'ERROR'
    if ($exactTargetDiscovered) {
      Set-WapcStageResult -Results $Context.stages -Stage 'Pairability' -Value 'UNKNOWN'
      Set-WapcStageResult -Results $Context.stages -Stage 'PairRequest' -Value 'NOT_RUN'
      Set-WapcStageResult -Results $Context.stages -Stage 'PairResult' -Value 'NOT_RUN'
    }
  } elseif ($exactTargetDiscovered -and $lastRank -and $lastRank.pairability -eq 'UNKNOWN' -and -not $pairSuccess) {
    if (-not $failure) {
      $failure = New-WapcFailure -Stage 'RANKING_CANDIDATES' -Classification 'PAIRABILITY_UNDETERMINED' `
        -Reason 'Enumeration incomplete; pairability cannot be determined for the configured target'
    }
    Set-WapcStageResult -Results $Context.stages -Stage 'PairRequest' -Value 'NOT_RUN'
    Set-WapcStageResult -Results $Context.stages -Stage 'PairResult' -Value 'NOT_RUN'
  } elseif ($exactTargetDiscovered -and $lastRank -and $lastRank.pairability -eq 'NOT_PAIRABLE' -and -not $pairSuccess -and -not $DiscoveryOnly) {
    if (-not $failure) {
      $failure = New-WapcFailure -Stage 'RANKING_CANDIDATES' -Classification 'DISCOVERABLE_NOT_PAIRABLE' `
        -Reason 'Exact target visible but no pairable Classic endpoint found'
    }
    Set-WapcStageResult -Results $Context.stages -Stage 'PairableEndpoint' -Value 'FAIL'
    Set-WapcStageResult -Results $Context.stages -Stage 'PairRequest' -Value 'NOT_RUN'
    Set-WapcStageResult -Results $Context.stages -Stage 'PairResult' -Value 'NOT_RUN'
  }

  if ($pairAttempted -and -not $pairSuccess -and -not $failure) {
    Set-WapcStageResult -Results $Context.stages -Stage 'PairResult' -Value 'FAIL'
    if (-not $failure) {
      $failure = New-WapcFailure -Stage 'PAIRING' -Classification 'PAIR_REQUEST_FAILED' `
        -Reason 'PairAsync was attempted but did not succeed'
    }
  }

  if ($pairSuccess -and -not $NoPair) {
    Set-WapcMachineState -Context $Context -State 'VERIFYING_AUDIO_PATH' -Log $Log
    $verification = Test-BluetoothPairVerification -Log $Log -WaitSeconds 30 `
      -NamePatterns ($namePatterns | ForEach-Object { $_ }) `
      -DeviceAddress $Context.target_address -TargetName $Context.target_name
    if ($verification.exact_target_audio_endpoint_found) {
      Set-WapcStageResult -Results $Context.stages -Stage 'AudioEndpoint' -Value 'PASS'
    } else {
      Set-WapcStageResult -Results $Context.stages -Stage 'AudioEndpoint' -Value 'FAIL'
      $pairSuccess = $false
      if (-not $failure) {
        $failure = New-WapcFailure -Stage 'VERIFYING_AUDIO_PATH' `
          -Classification 'PAIRING_SUCCEEDED_AUDIO_ENDPOINT_MISSING' `
          -Reason 'Pair/already-paired claimed but exact-target audio endpoint did not appear'
      }
    }
  } elseif (-not $pairAttempted) {
    Set-WapcStageResult -Results $Context.stages -Stage 'AudioEndpoint' -Value 'NOT_RUN'
  }

  $null = Repair-WapcStageResults -Results $Context.stages

  $audioReady = [bool]($verification -and $verification.exact_target_audio_endpoint_found)
  $pairResultStage = [string]$Context.stages.PairResult
  $invariantState = [ordered]@{
    pair_request                        = $(if ($Context.stages.PairRequest) { $Context.stages.PairRequest } else { $pairRequestOutcome })
    pair_result                         = $pairResultStage
    pairing_succeeded                   = ($pairResultStage -eq 'PASS')
    exact_target_already_paired         = $exactAlreadyPaired
    exact_target_discovered             = $exactTargetDiscovered
    exact_target_audio_endpoint_found   = $audioReady
    exact_target_a2dp_endpoint_found    = [bool]($verification -and $verification.exact_target_a2dp_endpoint_found)
    audio_endpoint_identity_match       = [bool]($verification -and $verification.audio_endpoint_identity_match)
    a2dp_endpoint_identity_match        = [bool]($verification -and $verification.a2dp_endpoint_identity_match)
    target_discovered_stage             = [string]$Context.stages.TargetDiscovered
    final_success                       = $false
    cleanup_removed_wrong_device        = $false
  }
  $invariantState.final_success = ($pairSuccess -and $audioReady)
  $violations = @((Test-WapcRecoveryInvariants -State $invariantState) | Where-Object { $_ -and $_.code })
  if ($violations.Count -gt 0) {
    $pairSuccess = $false
    $failure = New-WapcFailure -Stage 'STATE_VALIDATION' `
      -Classification 'INTERNAL_STATE_INVARIANT_FAILURE' `
      -Reason ('Invariant violations: ' + (($violations | ForEach-Object { $_.code }) -join ', ')) `
      -Evidence $violations
    if ($Context.stages.PairResult -eq 'PASS') {
      Set-WapcStageResult -Results $Context.stages -Stage 'PairResult' -Value 'NOT_RUN'
    }
  }

  $classification = if ($failure) {
    $failure.classification
  } elseif ($pairSuccess -and $audioReady) {
    'SUCCESS'
  } else {
    $null
  }
  if ($classification) { $Context.failure_classification = $classification }
  if ($failure) { [void]$Context.failures.Add($failure) }

  Write-WapcPairingDiagnosis -Context $Context -Enumeration $lastEnumeration `
    -Pairability $(if (-not $exactTargetDiscovered) { 'NOT_RUN' } elseif ($lastRank) { $lastRank.pairability } else { 'UNKNOWN' }) `
    -Classification $(if ($classification) { $classification } else { '' }) `
    -ObservedCandidates @($allCandidates.ToArray()) `
    -ExactTargetDiscovered:$exactTargetDiscovered

  Write-WapcDiagnosticReport -Context $Context -Candidates @($allCandidates.ToArray()) `
    -Enumeration $lastEnumeration -Pairing $pairingResult -Verification $verification `
    -DiscoveryMeta $discoveryMeta

  return [pscustomobject]@{
    pair_success = $pairSuccess
    pair_attempted = $pairAttempted
    pair_request_outcome = $pairRequestOutcome
    exact_target_discovered = $exactTargetDiscovered
    exact_target_already_paired = $exactAlreadyPaired
    selected_candidate = $selectedCandidate
    enumeration = $lastEnumeration
    rank = $lastRank
    verification = $verification
    failure = $failure
    classification = $classification
    invariant_violations = $violations
  }
}

Export-ModuleMember -Function @(
  'Invoke-WapcBluetoothPairing',
  'Set-WapcMachineState',
  'Write-WapcPairingDiagnosis'
)

Set-Alias -Name Invoke-BluetoothAutoPair -Value Invoke-WapcBluetoothPairing -Scope Local
