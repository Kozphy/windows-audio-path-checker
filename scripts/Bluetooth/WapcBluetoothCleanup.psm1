#Requires -Version 5.1
# Ghost-pair cleanup with verified postconditions (ASCII-only).
#
# Root cause of empty pnputil /remove-device:
#   Get-WapcTargetPnpNodes used `return ,@($selected.ToArray())`. When the selection
#   was empty, unary-comma wrapped an empty Object[] into a 1-element nested array.
#   Callers saw Count=1, foreach bound that nested empty array as $n, and
#   $n.InstanceId / FriendlyName / Class were null -> "Removing" + pnputil usage help.
# Fix: return a structured object with an explicit Nodes array; never invoke pnputil
# without a validated InstanceId; gate recovery on VERIFYING_CLEANUP.

Import-Module (Join-Path $PSScriptRoot 'WapcBluetoothCore.psm1') -Force -Global
Import-Module (Join-Path $PSScriptRoot 'WapcBluetoothIdentity.psm1') -Force -Global
Import-Module PnpDevice -ErrorAction SilentlyContinue

function Get-WapcPnpInstanceId {
  <#
  .SYNOPSIS
    Extract a non-empty PnP instance ID from a device object. Returns $null if missing.
  #>
  param($Device)
  if ($null -eq $Device) { return $null }

  # Nested/empty arrays from bad returns must never be treated as devices.
  if ($Device -is [System.Array]) {
    if ($Device.Length -eq 1 -and $null -ne $Device[0] -and -not ($Device[0] -is [System.Array])) {
      return (Get-WapcPnpInstanceId -Device $Device[0])
    }
    return $null
  }

  foreach ($name in @('InstanceId', 'InstanceID', 'PNPDeviceID', 'PnpDeviceID', 'DeviceID')) {
    try {
      if ($Device -is [System.Collections.IDictionary] -and $Device.Contains($name)) {
        $v = [string]$Device[$name]
      } else {
        $prop = $Device.PSObject.Properties[$name]
        if (-not $prop) { continue }
        $v = [string]$prop.Value
      }
      if (-not [string]::IsNullOrWhiteSpace($v)) {
        return $v.Trim()
      }
    } catch { }
  }
  return $null
}

function Test-WapcPnputilUsageOutput {
  param([string]$Output)
  if ([string]::IsNullOrWhiteSpace($Output)) { return $false }
  # Successful removals also print "Microsoft PnP Utility" — do not treat that alone as usage help.
  if ($Output -match '(?i)Device removed successfully') { return $false }
  if ($Output -match '(?i)already removed|not found|no devices were removed') { return $false }
  return [bool]($Output -match '(?i)PNPUTIL\s*\[|/add-driver|/enum-devices|Usage:')
}

function Invoke-WapcPnputilRemoveDevice {
  <#
  .SYNOPSIS
    Safely invoke pnputil /remove-device with a validated InstanceId.
  #>
  param(
    [Parameter(Mandatory)][string]$InstanceId,
    [scriptblock]$Log = { param($m) Write-Host $m }
  )

  $result = [ordered]@{
    Success       = $false
    InstanceId    = $InstanceId
    ExitCode      = $null
    Output        = @()
    VerifiedGone  = $false
    FailureReason = $null
    DurationMs    = 0
    Executable    = (Join-Path $env:SystemRoot 'System32\pnputil.exe')
  }

  if ([string]::IsNullOrWhiteSpace($InstanceId)) {
    $result.FailureReason = 'MISSING_INSTANCE_ID'
    & $Log 'PnP removal blocked: missing InstanceId'
    return [pscustomobject]$result
  }

  $exe = $result.Executable
  if (-not (Test-Path -LiteralPath $exe)) {
    $result.FailureReason = 'PNPUTIL_EXECUTION_FAILED'
    & $Log ('pnputil not found: ' + $exe)
    return [pscustomobject]$result
  }

  $sw = [System.Diagnostics.Stopwatch]::StartNew()
  try {
    $output = & $exe '/remove-device' "$InstanceId" 2>&1
    $result.ExitCode = $LASTEXITCODE
    $result.Output = @($output | ForEach-Object { "$_" })
  } catch {
    $result.FailureReason = 'PNPUTIL_EXECUTION_FAILED'
    $result.Output = @($_.Exception.Message)
    $sw.Stop()
    $result.DurationMs = [int]$sw.ElapsedMilliseconds
    return [pscustomobject]$result
  }
  $sw.Stop()
  $result.DurationMs = [int]$sw.ElapsedMilliseconds

  $joined = ($result.Output -join "`n")
  if (Test-WapcPnputilUsageOutput -Output $joined) {
    $result.FailureReason = 'PNPUTIL_USAGE_OUTPUT'
    return [pscustomobject]$result
  }
  if ($null -ne $result.ExitCode -and $result.ExitCode -ne 0) {
    $result.FailureReason = 'PNPUTIL_NONZERO_EXIT'
    return [pscustomobject]$result
  }

  $result.Success = $true
  $result.FailureReason = 'REMOVED_SUCCESSFULLY'
  return [pscustomobject]$result
}

function Remove-WapcPnpDeviceSafe {
  param(
    $Device,
    [string]$ExpectedAddress = '',
    [switch]$WhatIfPreference,
    [scriptblock]$Log = { param($m) Write-Host $m }
  )

  $instanceId = Get-WapcPnpInstanceId -Device $Device
  $friendly = ''
  $class = ''
  try { $friendly = [string]$Device.FriendlyName } catch { }
  try { $class = [string]$Device.Class } catch { }
  $obsAddr = Get-WapcAddressFromInstanceId $instanceId

  $result = [ordered]@{
    Stage         = 'PNP_REMOVAL'
    Success       = $false
    TargetAddress = (ConvertTo-WapcNormalizedBluetoothAddress $ExpectedAddress)
    InstanceId    = $instanceId
    FriendlyName  = $friendly
    Class         = $class
    ObservedAddress = $obsAddr
    ExitCode      = $null
    Verified      = $false
    Reason        = $null
    Output        = @()
  }

  if ([string]::IsNullOrWhiteSpace($instanceId)) {
    $result.Reason = 'MISSING_INSTANCE_ID'
    & $Log ('Cleanup candidate: ' + $(if ($friendly) { $friendly } else { '<unnamed>' }))
    & $Log ('  class=' + $class)
    & $Log '  instance_id=<missing>'
    & $Log ('  observed_address=' + $obsAddr)
    & $Log ('  expected_address=' + $result.TargetAddress)
    & $Log '  action=BLOCKED'
    & $Log '  reason=MISSING_INSTANCE_ID'
    if ($Device) {
      try { & $Log ('  object_type=' + $Device.GetType().FullName) } catch { & $Log '  object_type=<unknown>' }
    }
    return [pscustomobject]$result
  }

  & $Log ('Cleanup candidate: ' + $(if ($friendly) { $friendly } else { $instanceId }))
  & $Log ('  class=' + $class)
  & $Log ('  instance_id=' + $instanceId)
  & $Log ('  observed_address=' + $obsAddr)
  & $Log ('  expected_address=' + $result.TargetAddress)
  & $Log '  action=REMOVE'

  if ($WhatIfPreference) {
    $result.Success = $true
    $result.Reason = 'WHATIF_SKIPPED'
    & $Log '  WhatIf: pnputil not invoked'
    return [pscustomobject]$result
  }

  try {
    Remove-PnpDevice -InstanceId $instanceId -Confirm:$false -ErrorAction Stop
    $result.Success = $true
    $result.Reason = 'REMOVED_SUCCESSFULLY'
    & $Log '  Remove-PnpDevice: PASS'
  } catch {
    & $Log ('  Remove-PnpDevice failed: ' + $_.Exception.Message)
    $pnp = Invoke-WapcPnputilRemoveDevice -InstanceId $instanceId -Log $Log
    $result.ExitCode = $pnp.ExitCode
    $result.Output = @($pnp.Output)
    $result.Success = [bool]$pnp.Success
    $result.Reason = $pnp.FailureReason
    & $Log ('  pnputil exit_code=' + $pnp.ExitCode + ' reason=' + $pnp.FailureReason)
    if ($pnp.Output.Count -gt 0) {
      $msg = (($pnp.Output -join ' ') -replace '\s+', ' ').Trim()
      if ($msg.Length -gt 240) { $msg = $msg.Substring(0, 240) + '...' }
      & $Log ('  pnputil output: ' + $msg)
    }
  }

  return [pscustomobject]$result
}

function Get-WapcTargetPnpNodes {
  param(
    [string[]]$NamePatterns,
    [string]$DeviceAddress,
    [string]$TargetName = '',
    [scriptblock]$Log = $null
  )
  $addrPattern = ConvertTo-WapcNormalizedBluetoothAddress $DeviceAddress
  $identity = New-WapcTargetIdentity -RequestedName $TargetName -BluetoothAddress $DeviceAddress
  # Use List (not return ,@()) to avoid empty-array unary-comma Count=1 bug.
  $selected = New-Object 'System.Collections.Generic.List[object]'
  $skipped = New-Object 'System.Collections.Generic.List[object]'

  foreach ($dev in @(Get-PnpDevice -ErrorAction SilentlyContinue)) {
    $instanceId = Get-WapcPnpInstanceId -Device $dev
    $obsAddr = Get-WapcAddressFromInstanceId $instanceId
    $match = Test-WapcBluetoothIdentityMatch -ExpectedTarget $identity -ObservedDevice @{
      name           = $dev.FriendlyName
      InstanceId     = $instanceId
      device_address = $obsAddr
    }

    $entry = [ordered]@{
      FriendlyName       = [string]$dev.FriendlyName
      Class              = [string]$dev.Class
      Status             = [string]$dev.Status
      InstanceId         = $instanceId
      Address            = $obsAddr
      AddressSource      = $(if ($obsAddr) { 'InstanceId' } else { 'none' })
      IdentityConfidence = [string]$match.confidence
      IdentityMatched    = [bool]$match.matched
      AddressMatch       = [bool]$match.address_match
      Device             = $dev
    }

    if ($addrPattern -and $addrPattern.Length -ge 8) {
      if ($match.matched -and $match.address_match -and -not [string]::IsNullOrWhiteSpace($instanceId)) {
        $entry.Action = 'REMOVE_ELIGIBLE'
        $entry.Reason = 'ADDRESS_MATCH'
        [void]$selected.Add([pscustomobject]$entry)
      } else {
        $nameHint = $false
        foreach ($p in @($NamePatterns)) {
          if ($p -and $dev.FriendlyName -and ($dev.FriendlyName -match [regex]::Escape($p))) {
            $nameHint = $true; break
          }
        }
        # Only log interesting near-misses (name hint or other BT address), not every PnP node.
        if ($nameHint -or ($obsAddr -and $obsAddr.Length -ge 8)) {
          $entry.Action = 'SKIPPED_WRONG_DEVICE'
          $entry.Reason = $(if ($obsAddr -and $addrPattern -and $obsAddr -ne $addrPattern) {
            'ADDRESS_MISMATCH'
          } elseif ([string]::IsNullOrWhiteSpace($instanceId)) {
            'MISSING_INSTANCE_ID'
          } else {
            'INSUFFICIENT_IDENTITY'
          })
          [void]$skipped.Add([pscustomobject]$entry)
          if ($Log) {
            $nodeRole = 'NON_TARGET_DEVICE'
            if ($entry.Class -eq 'SoftwareDevice' -and $instanceId -like 'SWD\RADIO\BLUETOOTH_*') {
              $nodeRole = 'SOFTWARE_RADIO'
            } elseif ($entry.Class -eq 'Bluetooth' -and $entry.FriendlyName -match 'Generic Access Profile|Generic Attribute Profile|GATT|BLE') {
              $nodeRole = 'BLE_DEVICE'
            }
            & $Log ('Observed Bluetooth node: ' + $entry.FriendlyName)
            & $Log ('  class=' + $entry.Class)
            & $Log ('  instance_id=' + $(if ($instanceId) { $instanceId } else { '<missing>' }))
            & $Log ('  observed_address=' + $obsAddr)
            & $Log ('  expected_address=' + $addrPattern)
            & $Log ('  candidate_role=' + $nodeRole)
            & $Log ('  identity_result=DIFFERENT_DEVICE')
            & $Log ('  cleanup_eligible=false')
            & $Log ('  action=SKIP')
            & $Log ('  reason=CONFIGURED_TARGET_MISMATCH')
          }
        }
      }
      continue
    }

    if ($match.matched -and $match.name_match -and -not [string]::IsNullOrWhiteSpace($instanceId)) {
      $entry.Action = 'REMOVE_ELIGIBLE'
      $entry.Reason = 'EXACT_NAME_NO_ADDRESS'
      [void]$selected.Add([pscustomobject]$entry)
    }
  }

  return [pscustomobject]@{
    Nodes   = @($selected.ToArray())
    Skipped = @($skipped.ToArray())
    Count   = $selected.Count
  }
}

function Remove-WapcBluetoothGhostAssociation {
  param(
    [Parameter(Mandatory)]$Context,
    [string[]]$NamePatterns,
    [string]$DeviceAddress,
    [switch]$WhatIfPreference,
    [scriptblock]$Log = { param($m) Write-Host $m }
  )

  $addr = ConvertTo-WapcNormalizedBluetoothAddress $DeviceAddress

  $report = [ordered]@{
    matching_pnp_nodes   = 0
    pnp_removal          = 'NOT_REQUIRED'
    registry_cleanup     = 'NOT_REQUIRED'
    keys_cleanup         = 'NOT_REQUIRED'
    remaining_nodes      = 0
    postcondition        = 'NOT_RUN'
    cleanup_verified     = $false
    operations           = (New-Object System.Collections.ArrayList)
    removal_results      = (New-Object System.Collections.ArrayList)
  }

  if (-not $Context.is_elevated) {
    $report.pnp_removal = 'SKIPPED'
    $report.registry_cleanup = 'SKIPPED'
    $report.postcondition = 'BLOCKED'
    return [pscustomobject]$report
  }

  Get-Process SystemSettings, DevicePairingWizard -ErrorAction SilentlyContinue |
    Stop-Process -Force -ErrorAction SilentlyContinue
  & $Log 'Closed Settings/pairing UI'

  $beforeQuery = Get-WapcTargetPnpNodes -NamePatterns $NamePatterns -DeviceAddress $DeviceAddress `
    -TargetName $Context.target_name -Log $Log
  $nodesBefore = @($beforeQuery.Nodes)
  $report.matching_pnp_nodes = $nodesBefore.Count
  & $Log ('Matching PnP nodes: ' + $nodesBefore.Count)

  # Registry Devices key
  $regStatus = 'NOT_REQUIRED'
  if ($WhatIfPreference) {
    $regStatus = 'SKIPPED'
    & $Log ('WhatIf: would clear Devices\' + $addr)
  } else {
    try {
      $hk = [Microsoft.Win32.Registry]::LocalMachine.OpenSubKey(
        'SYSTEM\CurrentControlSet\Services\BTHPORT\Parameters\Devices', $true
      )
      if ($hk -and ($hk.GetSubKeyNames() -contains $addr)) {
        $hk.DeleteSubKeyTree($addr)
        $regStatus = 'SUCCEEDED'
        & $Log ('Cleared Devices\' + $addr)
      } else {
        $regStatus = 'NOT_REQUIRED'
        & $Log ('No Devices\' + $addr + ' key')
      }
      if ($hk) { $hk.Close() }
    } catch {
      $regStatus = 'FAILED'
      & $Log ('Registry cleanup failed: ' + $_.Exception.Message)
      Add-WapcError -Context $Context -Stage 'GhostCleanup' -Message $_.Exception.Message
    }
  }
  $report.registry_cleanup = $regStatus
  [void]$report.operations.Add([ordered]@{ op = 'registry_devices'; status = $regStatus })

  # Link keys (scoped to target address only)
  $keysStatus = 'NOT_REQUIRED'
  if (-not $WhatIfPreference) {
    $keysRoot = 'HKLM\SYSTEM\CurrentControlSet\Services\BTHPORT\Parameters\Keys'
    $regOut = & reg.exe query $keysRoot 2>&1
    if ($LASTEXITCODE -eq 0) {
      $cleared = $false
      foreach ($line in ($regOut | Where-Object { $_ -match 'HKEY_LOCAL_MACHINE' })) {
        $sub = ($line -replace 'HKEY_LOCAL_MACHINE\\', 'HKLM\').Trim()
        $target = $sub + '\' + $addr
        & reg.exe delete $target /f 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
          $cleared = $true
          & $Log ('Cleared linkkey ' + $target)
        }
      }
      $keysStatus = if ($cleared) { 'SUCCEEDED' } else { 'NOT_REQUIRED' }
    } else {
      $keysStatus = 'SKIPPED'
      & $Log 'Link keys enumeration unavailable (protected or empty)'
    }
  } else {
    $keysStatus = 'SKIPPED'
  }
  $report.keys_cleanup = $keysStatus
  [void]$report.operations.Add([ordered]@{ op = 'registry_link_keys'; status = $keysStatus })

  # PnP removal — only nodes with validated InstanceId
  $pnpStatus = if ($nodesBefore.Count -eq 0) { 'NOT_REQUIRED' } else { 'ATTEMPTED' }
  $anyMissingId = $false
  $anyFailed = $false
  foreach ($n in $nodesBefore) {
    $deviceObj = if ($n.PSObject.Properties['Device']) { $n.Device } else { $n }
    $removal = Remove-WapcPnpDeviceSafe -Device $deviceObj -ExpectedAddress $addr `
      -WhatIfPreference:$WhatIfPreference -Log $Log
    [void]$report.removal_results.Add($removal)
    if ($removal.Reason -eq 'MISSING_INSTANCE_ID') {
      $anyMissingId = $true
      $anyFailed = $true
    } elseif (-not $removal.Success) {
      $anyFailed = $true
      Add-WapcError -Context $Context -Stage 'GhostCleanup' -Message (
        'PnP removal failed: ' + $removal.Reason + ' id=' + $removal.InstanceId
      )
    }
  }

  & $Log 'STATE VERIFYING_CLEANUP'
  $afterQuery = Get-WapcTargetPnpNodes -NamePatterns $NamePatterns -DeviceAddress $DeviceAddress `
    -TargetName $Context.target_name
  $nodesAfter = @($afterQuery.Nodes)
  $report.remaining_nodes = $nodesAfter.Count
  & $Log ('Remaining target PnP nodes: ' + $nodesAfter.Count)

  # Mark still-present removals
  foreach ($removal in @($report.removal_results)) {
    if ($removal.Success -and $removal.InstanceId -and -not $WhatIfPreference) {
      $still = @($nodesAfter | Where-Object {
        (Get-WapcPnpInstanceId -Device $_) -eq $removal.InstanceId -or
        $_.InstanceId -eq $removal.InstanceId
      })
      if ($still.Count -gt 0) {
        $removal.Success = $false
        $removal.Verified = $false
        $removal.Reason = 'DEVICE_STILL_PRESENT'
        $anyFailed = $true
        & $Log ('  DEVICE_STILL_PRESENT: ' + $removal.InstanceId)
      } else {
        $removal.Verified = $true
      }
    }
  }

  if ($nodesBefore.Count -eq 0) {
    $pnpStatus = 'NOT_REQUIRED'
  } elseif ($anyMissingId) {
    $pnpStatus = 'FAILED'
  } elseif ($anyFailed -or $nodesAfter.Count -gt 0) {
    $pnpStatus = 'FAILED'
  } else {
    $pnpStatus = 'SUCCEEDED'
  }
  $report.pnp_removal = $pnpStatus
  [void]$report.operations.Add([ordered]@{ op = 'pnp_remove'; status = $pnpStatus })

  # Case J: no matching device / already absent => clean (idempotent), not failure.
  # Desired end-state is zero target PnP nodes. Prefer outcome over intermediate flags.
  if ($nodesAfter.Count -eq 0 -and $regStatus -ne 'FAILED') {
    $report.postcondition = 'PASS'
    $report.cleanup_verified = $true
    if ($nodesBefore.Count -eq 0) {
      & $Log 'Cleanup verification: PASS (already clean)'
    } else {
      & $Log 'Cleanup verification: PASS'
    }
  } elseif (-not $Context.is_elevated) {
    $report.postcondition = 'BLOCKED'
    $report.cleanup_verified = $false
    & $Log 'Cleanup verification: BLOCKED'
  } else {
    $report.postcondition = 'FAIL'
    $report.cleanup_verified = $false
    & $Log 'Cleanup verification: FAIL'
  }

  return [pscustomobject]$report
}

function Restart-WapcBluetoothAdapter {
  param(
    [Parameter(Mandatory)]$Context,
    [Parameter(Mandatory)][string]$AdapterInstanceId,
    [switch]$WhatIfPreference,
    [scriptblock]$Log = { param($m) Write-Host $m }
  )
  if (-not $Context.is_elevated) {
    return [ordered]@{ status = 'BLOCKED'; adapter_status = $null }
  }
  if ($WhatIfPreference) {
    & $Log 'WhatIf: would bounce Bluetooth adapter'
    return [ordered]@{ status = 'SKIPPED'; adapter_status = 'UNKNOWN' }
  }
  try {
    Disable-PnpDevice -InstanceId $AdapterInstanceId -Confirm:$false -ErrorAction Stop
    & $Log 'Adapter disabled'
    Start-Sleep -Seconds 5
    Enable-PnpDevice -InstanceId $AdapterInstanceId -Confirm:$false -ErrorAction Stop
    & $Log 'Adapter enabled'
  } catch {
    & $Log ('Adapter toggle failed: ' + $_.Exception.Message)
    try {
      Enable-PnpDevice -InstanceId $AdapterInstanceId -Confirm:$false -ErrorAction Stop
      & $Log 'Enable-only recovery attempted'
    } catch {
      Add-WapcError -Context $Context -Stage 'AdapterReset' -Message $_.Exception.Message
    }
  }
  Start-Sleep -Seconds 3
  $a = Get-PnpDevice -ErrorAction SilentlyContinue |
    Where-Object InstanceId -eq $AdapterInstanceId | Select-Object -First 1
  $st = if ($a) { $a.Status } else { 'MISSING' }
  & $Log ('Adapter final status: ' + $st)
  return [ordered]@{
    status         = if ($st -eq 'OK') { 'PASS' } else { 'FAIL' }
    adapter_status = $st
  }
}

Export-ModuleMember -Function @(
  'Get-WapcPnpInstanceId',
  'Test-WapcPnputilUsageOutput',
  'Invoke-WapcPnputilRemoveDevice',
  'Remove-WapcPnpDeviceSafe',
  'Get-WapcTargetPnpNodes',
  'Remove-WapcBluetoothGhostAssociation',
  'Restart-WapcBluetoothAdapter'
)
