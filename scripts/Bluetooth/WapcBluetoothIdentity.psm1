#Requires -Version 5.1
# Canonical Bluetooth target identity policy (mirrors audio_path_checker.bluetooth_pairing.identity).
#
# Root cause of false-positive SUCCESS: sibling headsets (same brand) were treated as the
# recovery target when ranking/verification used friendly-name or "any Bluetooth audio"
# evidence. Address-aware identity matching is mandatory when a target address is known.

function ConvertTo-WapcNormalizedBluetoothAddress {
  param([string]$Value)
  if (-not $Value) { return '' }
  $hex = ($Value -replace '[^0-9a-fA-F]', '').ToLowerInvariant()
  if ($hex.Length -gt 12) { $hex = $hex.Substring($hex.Length - 12) }
  return $hex
}

function ConvertTo-WapcNormalizedDeviceName {
  param([string]$Value)
  if (-not $Value) { return '' }
  return (($Value -replace '\s+', ' ').Trim()).ToLowerInvariant()
}

# Compatibility aliases (approved verb names are canonical).
Set-Alias -Name Normalize-WapcBluetoothAddress -Value ConvertTo-WapcNormalizedBluetoothAddress
Set-Alias -Name Normalize-WapcDeviceName -Value ConvertTo-WapcNormalizedDeviceName

function Get-WapcAddressFromInstanceId {
  param([string]$InstanceId)
  if (-not $InstanceId) { return '' }
  if ($InstanceId -match '(?:DEV_|BluetoothDevice_|_)([0-9A-Fa-f]{12})(?:_|$|\\)') {
    return (ConvertTo-WapcNormalizedBluetoothAddress $Matches[1])
  }
  if ($InstanceId -match '([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}') {
    return (ConvertTo-WapcNormalizedBluetoothAddress $Matches[0])
  }
  return ''
}

function New-WapcTargetIdentity {
  param(
    [string]$RequestedName,
    [string]$BluetoothAddress = ''
  )
  $addr = ConvertTo-WapcNormalizedBluetoothAddress $BluetoothAddress
  return [ordered]@{
    requested_name               = $RequestedName
    normalized_name              = (ConvertTo-WapcNormalizedDeviceName $RequestedName)
    bluetooth_address            = $addr
    normalized_bluetooth_address = $addr
    pnp_instance_ids             = @()
    association_endpoint_ids     = @()
    container_ids                = @()
    audio_endpoint_ids           = @()
  }
}

function Test-WapcBluetoothIdentityMatch {
  <#
  .SYNOPSIS
    Structured identity comparison. Exact Bluetooth address dominates when known.
  #>
  param(
    [Parameter(Mandatory)]$ExpectedTarget,
    [Parameter(Mandatory)]$ObservedDevice
  )

  $expAddr = ''
  $expName = ''
  if ($ExpectedTarget -is [string]) {
    $expName = ConvertTo-WapcNormalizedDeviceName $ExpectedTarget
  } elseif ($ExpectedTarget -is [System.Collections.IDictionary]) {
    $expAddr = ConvertTo-WapcNormalizedBluetoothAddress (
      [string]($ExpectedTarget['normalized_bluetooth_address'] -as [string])
    )
    if (-not $expAddr) {
      $expAddr = ConvertTo-WapcNormalizedBluetoothAddress ([string]$ExpectedTarget['bluetooth_address'])
    }
    $expName = ConvertTo-WapcNormalizedDeviceName (
      [string]($ExpectedTarget['normalized_name'] -as [string])
    )
    if (-not $expName) {
      $expName = ConvertTo-WapcNormalizedDeviceName ([string]$ExpectedTarget['requested_name'])
    }
  } else {
    $expAddr = ConvertTo-WapcNormalizedBluetoothAddress ([string]$ExpectedTarget.bluetooth_address)
    if (-not $expAddr) {
      $expAddr = ConvertTo-WapcNormalizedBluetoothAddress ([string]$ExpectedTarget.normalized_bluetooth_address)
    }
    $expName = ConvertTo-WapcNormalizedDeviceName ([string]$ExpectedTarget.normalized_name)
    if (-not $expName) {
      $expName = ConvertTo-WapcNormalizedDeviceName ([string]$ExpectedTarget.requested_name)
    }
  }

  $obsAddr = ''
  $obsName = ''
  if ($ObservedDevice -is [System.Collections.IDictionary]) {
    $obsAddr = ConvertTo-WapcNormalizedBluetoothAddress ([string]$ObservedDevice['device_address'])
    if (-not $obsAddr) { $obsAddr = ConvertTo-WapcNormalizedBluetoothAddress ([string]$ObservedDevice['address']) }
    if (-not $obsAddr) { $obsAddr = ConvertTo-WapcNormalizedBluetoothAddress ([string]$ObservedDevice['bluetooth_address']) }
    if (-not $obsAddr) {
      $obsAddr = Get-WapcAddressFromInstanceId ([string]$ObservedDevice['id'])
    }
    if (-not $obsAddr) {
      $obsAddr = Get-WapcAddressFromInstanceId ([string]$ObservedDevice['InstanceId'])
    }
    if (-not $obsAddr) {
      $obsAddr = Get-WapcAddressFromInstanceId ([string]$ObservedDevice['instance_id'])
    }
    $obsName = ConvertTo-WapcNormalizedDeviceName ([string]$ObservedDevice['name'])
    if (-not $obsName) {
      $obsName = ConvertTo-WapcNormalizedDeviceName ([string]$ObservedDevice['FriendlyName'])
    }
  } else {
    $obsAddr = ConvertTo-WapcNormalizedBluetoothAddress ([string]$ObservedDevice.device_address)
    if (-not $obsAddr) { $obsAddr = ConvertTo-WapcNormalizedBluetoothAddress ([string]$ObservedDevice.address) }
    if (-not $obsAddr) { $obsAddr = Get-WapcAddressFromInstanceId ([string]$ObservedDevice.id) }
    if (-not $obsAddr) { $obsAddr = Get-WapcAddressFromInstanceId ([string]$ObservedDevice.InstanceId) }
    $obsName = ConvertTo-WapcNormalizedDeviceName ([string]$ObservedDevice.name)
    if (-not $obsName) { $obsName = ConvertTo-WapcNormalizedDeviceName ([string]$ObservedDevice.FriendlyName) }
  }

  $addressMatch = ($expAddr -and $obsAddr -and ($expAddr -eq $obsAddr))
  $nameMatch = ($expName -and $obsName -and ($expName -eq $obsName))

  $result = [ordered]@{
    matched           = $false
    confidence        = 'NONE'
    expected_address  = $expAddr
    observed_address  = $obsAddr
    expected_name     = $expName
    observed_name     = $obsName
    address_match     = [bool]$addressMatch
    name_match        = [bool]$nameMatch
    reason            = 'NO_OBSERVED_IDENTITY'
    match_method      = $null
  }

  if ($expAddr -and $obsAddr) {
    if ($addressMatch) {
      $result.matched = $true
      $result.confidence = 'EXACT'
      $result.reason = 'BLUETOOTH_ADDRESS_MATCH'
      $result.match_method = 'bluetooth_address'
      return [pscustomobject]$result
    }
    $result.reason = 'BLUETOOTH_ADDRESS_MISMATCH'
    $result.match_method = 'bluetooth_address'
    return [pscustomobject]$result
  }

  if ($expAddr -and -not $obsAddr) {
    $result.reason = 'EXPECTED_ADDRESS_REQUIRED_OBSERVED_ADDRESS_MISSING'
    $result.confidence = if ($nameMatch) { 'LOW' } else { 'NONE' }
    $result.match_method = if ($nameMatch) { 'name_insufficient_without_address' } else { $null }
    return [pscustomobject]$result
  }

  if ($nameMatch) {
    $result.matched = $true
    $result.confidence = 'MEDIUM'
    $result.reason = 'EXACT_NAME_NO_ADDRESS'
    $result.match_method = 'exact_normalized_name'
    return [pscustomobject]$result
  }

  $result.reason = if ($obsName) { 'NAME_MISMATCH' } else { 'NO_OBSERVED_IDENTITY' }
  return [pscustomobject]$result
}

function Add-WapcCandidateIdentityAnnotation {
  param(
    $Candidate,
    [string]$TargetName,
    [string]$TargetAddress
  )
  $identity = New-WapcTargetIdentity -RequestedName $TargetName -BluetoothAddress $TargetAddress
  $match = Test-WapcBluetoothIdentityMatch -ExpectedTarget $identity -ObservedDevice $Candidate
  if ($Candidate -is [System.Collections.IDictionary]) {
    $Candidate['identity_match'] = $match
    $Candidate['identity_matched'] = [bool]$match.matched
    if ($match.reason -eq 'BLUETOOTH_ADDRESS_MISMATCH') {
      $Candidate['disposition'] = 'REJECTED_WRONG_DEVICE'
      $Candidate['rejection_reason'] = 'CONFIGURED_TARGET_MISMATCH'
      $Candidate['candidate_role'] = 'NON_TARGET_DEVICE'
      $Candidate['identity_result'] = 'DIFFERENT_DEVICE'
      $Candidate['action'] = 'SKIP'
    } elseif (-not $match.matched) {
      $Candidate['disposition'] = 'REJECTED_INSUFFICIENT_IDENTITY'
      $Candidate['rejection_reason'] = [string]$match.reason
    } else {
      $Candidate['disposition'] = 'ACCEPTED'
      $Candidate['rejection_reason'] = $null
    }
    return $Candidate
  }
  $Candidate | Add-Member -NotePropertyName identity_match -NotePropertyValue $match -Force
  $Candidate | Add-Member -NotePropertyName identity_matched -NotePropertyValue ([bool]$match.matched) -Force
  $disposition = if ($match.reason -eq 'BLUETOOTH_ADDRESS_MISMATCH') {
    'REJECTED_WRONG_DEVICE'
  } elseif (-not $match.matched) {
    'REJECTED_INSUFFICIENT_IDENTITY'
  } else {
    'ACCEPTED'
  }
  $Candidate | Add-Member -NotePropertyName disposition -NotePropertyValue $disposition -Force
  $rejectionReason = if ($disposition -eq 'ACCEPTED') {
    $null
  } elseif ($match.reason -eq 'BLUETOOTH_ADDRESS_MISMATCH') {
    'CONFIGURED_TARGET_MISMATCH'
  } else {
    [string]$match.reason
  }
  $Candidate | Add-Member -NotePropertyName rejection_reason -NotePropertyValue $rejectionReason -Force
  if ($disposition -eq 'REJECTED_WRONG_DEVICE') {
    $Candidate | Add-Member -NotePropertyName candidate_role -NotePropertyValue 'NON_TARGET_DEVICE' -Force
    $Candidate | Add-Member -NotePropertyName identity_result -NotePropertyValue 'DIFFERENT_DEVICE' -Force
    $Candidate | Add-Member -NotePropertyName action -NotePropertyValue 'SKIP' -Force
  }
  return $Candidate
}

function Test-WapcRecoveryInvariants {
  param($State)
  $violations = New-Object System.Collections.ArrayList
  $pairRequest = ([string]$State.pair_request).ToUpperInvariant()
  $pairResult = if ($State.pair_result) { ([string]$State.pair_result).ToUpperInvariant() } else { '' }
  $pairingSucceeded = [bool]$State.pairing_succeeded
  if (-not $pairResult -and $pairingSucceeded) {
    $pairResult = 'PASS'
  }
  $exactAlreadyPaired = [bool]$State.exact_target_already_paired
  $exactDiscovered = [bool]$State.exact_target_discovered
  $exactAudio = [bool]$State.exact_target_audio_endpoint_found
  $exactA2dp = [bool]$State.exact_target_a2dp_endpoint_found
  $audioIdentity = [bool]$State.audio_endpoint_identity_match
  $a2dpIdentity = [bool]$State.a2dp_endpoint_identity_match
  $finalSuccess = [bool]$State.final_success
  $targetDiscoveredStage = ([string]$State.target_discovered_stage).ToUpperInvariant()

  if ($pairResult -eq 'PASS' -and ($pairRequest -in @('NOT_RUN', 'NOT_ATTEMPTED')) -and (-not $exactAlreadyPaired)) {
    [void]$violations.Add([ordered]@{
      invariant = 1
      code = 'PAIRING_SUCCEEDED_WITHOUT_REQUEST_OR_ALREADY_PAIRED'
    })
  }
  if ($pairResult -eq 'FAIL' -and ($pairRequest -in @('NOT_RUN', 'NOT_ATTEMPTED'))) {
    [void]$violations.Add([ordered]@{
      invariant = 7
      code = 'PAIR_RESULT_FAIL_WITHOUT_REQUEST'
    })
  }
  if ($pairRequest -in @('NOT_RUN', 'NOT_ATTEMPTED', 'NOT_REQUIRED', 'BLOCKED', 'SKIPPED') -and $pairResult -in @('PASS', 'FAIL', 'ERROR')) {
    if ($pairResult -eq 'PASS' -and $pairRequest -eq 'NOT_REQUIRED' -and $exactAlreadyPaired) {
      # NOT_REQUIRED + PASS is valid for already-paired shortcut
    } elseif ($pairResult -eq 'PASS' -and $exactAlreadyPaired) {
      # allowed
    } elseif ($pairResult -ne 'PASS' -or $pairRequest -notin @('NOT_REQUIRED')) {
      if (-not ($pairResult -eq 'PASS' -and $pairRequest -eq 'NOT_REQUIRED' -and $exactAlreadyPaired)) {
        if ($pairResult -in @('PASS', 'FAIL', 'ERROR') -and $pairRequest -in @('NOT_RUN', 'NOT_ATTEMPTED')) {
          [void]$violations.Add([ordered]@{
            invariant = 8
            code = 'PAIR_RESULT_SET_WITHOUT_PAIR_REQUEST'
          })
        }
      }
    }
  }
  if ($targetDiscoveredStage -in @('FAIL', 'NOT_FOUND') -and ($pairRequest -in @('PASS', 'ATTEMPTED'))) {
    [void]$violations.Add([ordered]@{
      invariant = 9
      code = 'PAIR_REQUEST_WITHOUT_TARGET_DISCOVERY'
    })
  }
  if ($exactAudio -and -not $audioIdentity) {
    [void]$violations.Add([ordered]@{ invariant = 2; code = 'AUDIO_ENDPOINT_WITHOUT_IDENTITY' })
  }
  if ($exactA2dp -and -not $a2dpIdentity) {
    [void]$violations.Add([ordered]@{ invariant = 3; code = 'A2DP_ENDPOINT_WITHOUT_IDENTITY' })
  }
  if ((-not $exactDiscovered) -and ([string]$State.target_discovered_stage -eq 'PASS')) {
    [void]$violations.Add([ordered]@{ invariant = 4; code = 'TARGET_DISCOVERED_WITHOUT_IDENTITY' })
  }
  if ($finalSuccess -and -not $exactDiscovered) {
    [void]$violations.Add([ordered]@{ invariant = 5; code = 'SUCCESS_WITHOUT_EXACT_TARGET' })
  }
  if ([bool]$State.cleanup_removed_wrong_device) {
    [void]$violations.Add([ordered]@{ invariant = 6; code = 'CLEANUP_REMOVED_WRONG_DEVICE' })
  }
  # Return a flat array. Never use unary comma on empty arrays (Count becomes 1).
  if ($violations.Count -eq 0) { return @() }
  return @($violations.ToArray())
}

function Test-WapcRecoveryState {
  param($State)
  $v = @(Test-WapcRecoveryInvariants -State $State)
  if ($v.Count -eq 0) { return @() }
  return @($v)
}

function Get-WapcExitCodeForClassification {
  param([string]$Classification)
  switch ($Classification) {
    'SUCCESS' { return 0 }
    'TARGET_NOT_DISCOVERED' { return 10 }
    'TARGET_IDENTITY_MISMATCH' { return 11 }
    'TARGET_NOT_PAIRABLE' { return 12 }
    'DISCOVERABLE_NOT_PAIRABLE' { return 12 }
    'PAIRING_FAILED' { return 13 }
    'PAIR_REQUEST_FAILED' { return 13 }
    'PAIRING_REJECTED' { return 13 }
    'PAIRING_TIMEOUT' { return 14 }
    'PNP_PATH_MISSING' { return 20 }
    'A2DP_PATH_MISSING' { return 21 }
    'A2DP_ENDPOINT_TIMEOUT' { return 21 }
    'AUDIO_ENDPOINT_MISSING' { return 22 }
    'AUDIO_ENDPOINT_TIMEOUT' { return 22 }
    'PAIRING_SUCCEEDED_AUDIO_ENDPOINT_MISSING' { return 22 }
    'DISCOVERY_API_UNAVAILABLE' { return 30 }
    'DISCOVERY_ENUMERATION_FAILED' { return 30 }
    'CLASSIC_ENDPOINT_ENUMERATION_FAILED' { return 30 }
    'SERVICE_FAILURE' { return 31 }
    'SERVICE_CONTROL_FAILED' { return 31 }
    'ADAPTER_FAILURE' { return 32 }
    'ADAPTER_RESET_FAILED' { return 32 }
    'CLEANUP_FAILURE' { return 40 }
    'GHOST_CLEANUP_FAILED' { return 40 }
    'INTERNAL_STATE_INVARIANT_FAILURE' { return 90 }
    default { return 1 }
  }
}

Export-ModuleMember -Function @(
  'ConvertTo-WapcNormalizedBluetoothAddress',
  'ConvertTo-WapcNormalizedDeviceName',
  'Get-WapcAddressFromInstanceId',
  'New-WapcTargetIdentity',
  'Test-WapcBluetoothIdentityMatch',
  'Add-WapcCandidateIdentityAnnotation',
  'Test-WapcRecoveryInvariants',
  'Test-WapcRecoveryState',
  'Get-WapcExitCodeForClassification'
)
Export-ModuleMember -Alias @(
  'Normalize-WapcBluetoothAddress',
  'Normalize-WapcDeviceName'
)
