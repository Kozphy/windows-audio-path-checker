#Requires -Version 5.1
# Ghost-pair cleanup with verified postconditions (ASCII-only).

# -Global prevents nested-module steal of Core exports from the caller session.
Import-Module (Join-Path $PSScriptRoot 'WapcBluetoothCore.psm1') -Force -Global

function Get-WapcTargetPnpNodes {
  param(
    [string[]]$NamePatterns,
    [string]$DeviceAddress
  )
  $addrPattern = $DeviceAddress -replace '[^0-9a-fA-F]', ''
  if ($addrPattern.Length -ge 12) {
    $addrPattern = $addrPattern.Substring($addrPattern.Length - 12)
  }
  return @(
    Get-PnpDevice -ErrorAction SilentlyContinue | Where-Object {
      $nameOk = $false
      foreach ($p in $NamePatterns) {
        if ($_.FriendlyName -match [regex]::Escape($p)) { $nameOk = $true; break }
      }
      $addrOk = ($_.InstanceId -match $addrPattern)
      $nameOk -or $addrOk
    }
  )
}

function Remove-WapcBluetoothGhostAssociation {
  param(
    [Parameter(Mandatory)]$Context,
    [string[]]$NamePatterns,
    [string]$DeviceAddress,
    [switch]$WhatIfPreference,
    [scriptblock]$Log = { param($m) Write-Host $m }
  )

  $addr = ($DeviceAddress -replace '[^0-9a-fA-F]', '').ToLowerInvariant()
  if ($addr.Length -gt 12) { $addr = $addr.Substring($addr.Length - 12) }

  $report = [ordered]@{
    matching_pnp_nodes   = 0
    pnp_removal          = 'NOT_REQUIRED'
    registry_cleanup     = 'NOT_REQUIRED'
    keys_cleanup         = 'NOT_REQUIRED'
    remaining_nodes      = 0
    postcondition        = 'NOT_RUN'
    operations           = @()
  }

  if (-not $Context.is_elevated) {
    $report.pnp_removal = 'SKIPPED'
    $report.registry_cleanup = 'SKIPPED'
    $report.postcondition = 'BLOCKED'
    return $report
  }

  Get-Process SystemSettings, DevicePairingWizard -ErrorAction SilentlyContinue |
    Stop-Process -Force -ErrorAction SilentlyContinue
  & $Log 'Closed Settings/pairing UI'

  $nodesBefore = @(Get-WapcTargetPnpNodes -NamePatterns $NamePatterns -DeviceAddress $DeviceAddress)
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

  # PnP removal
  $pnpStatus = if ($nodesBefore.Count -eq 0) { 'NOT_REQUIRED' } else { 'ATTEMPTED' }
  foreach ($n in $nodesBefore) {
    if ($WhatIfPreference) {
      & $Log ('WhatIf: would remove ' + $n.FriendlyName)
      continue
    }
    & $Log ('Removing ' + $n.Class + ' ' + $n.FriendlyName)
    try {
      Remove-PnpDevice -InstanceId $n.InstanceId -Confirm:$false -ErrorAction Stop
      & $Log '  PnP removed'
    } catch {
      $r = & pnputil.exe /remove-device $n.InstanceId 2>&1 | Out-String
      $msg = ($r -replace '\s+', ' ').Trim()
      & $Log ('  pnputil: ' + $msg)
      if ($msg -match 'Access is denied|Failed') {
        $pnpStatus = 'FAILED'
        Add-WapcError -Context $Context -Stage 'GhostCleanup' -Message $msg
      }
    }
  }
  if ($nodesBefore.Count -eq 0) {
    $pnpStatus = 'NOT_REQUIRED'
  } elseif ($pnpStatus -eq 'ATTEMPTED') {
    $pnpStatus = 'SUCCEEDED'
  }
  $report.pnp_removal = $pnpStatus
  [void]$report.operations.Add([ordered]@{ op = 'pnp_remove'; status = $pnpStatus })

  $nodesAfter = @(Get-WapcTargetPnpNodes -NamePatterns $NamePatterns -DeviceAddress $DeviceAddress)
  $report.remaining_nodes = $nodesAfter.Count
  & $Log ('Remaining PnP nodes: ' + $nodesAfter.Count)

  if ($nodesBefore.Count -eq 0 -and $regStatus -in @('SUCCEEDED', 'NOT_REQUIRED', 'SKIPPED')) {
    $report.postcondition = 'PASS'
  } elseif ($nodesAfter.Count -eq 0 -and $regStatus -ne 'FAILED') {
    $report.postcondition = 'PASS'
  } elseif (-not $Context.is_elevated) {
    $report.postcondition = 'BLOCKED'
  } else {
    $report.postcondition = 'FAIL'
  }

  return $report
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
  'Get-WapcTargetPnpNodes',
  'Remove-WapcBluetoothGhostAssociation',
  'Restart-WapcBluetoothAdapter'
)
