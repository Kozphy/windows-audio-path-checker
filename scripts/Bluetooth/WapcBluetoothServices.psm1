#Requires -Version 5.1
# Truthful Bluetooth service control (ASCII-only).

# -Global prevents nested-module steal of Core exports from the caller session.
Import-Module (Join-Path $PSScriptRoot 'WapcBluetoothCore.psm1') -Force -Global

function Test-WapcBluetoothServices {
  param(
    [Parameter(Mandatory)]$Context,
    [string[]]$ServiceNames = @('bthserv', 'BTAGService', 'BthAvctpSvc', 'DeviceAssociationService'),
    [switch]$WhatIfPreference,
    [switch]$Restart,
    [scriptblock]$Log = { param($m) Write-Host $m }
  )

  $details = New-Object System.Collections.ArrayList
  foreach ($name in $ServiceNames) {
    $before = $null
    try { $before = (Get-Service $name -ErrorAction Stop).Status } catch { $before = 'Unavailable' }

    $commandResult = 'NOT_RUN'
    if ($Restart -and -not $WhatIfPreference -and $Context.is_elevated) {
      try {
        Restart-Service $name -Force -ErrorAction Stop
        $commandResult = 'PASS'
        & $Log ('Restarted ' + $name)
      } catch {
        try {
          Start-Service $name -ErrorAction Stop
          $commandResult = 'PASS'
          & $Log ('Started ' + $name)
        } catch {
          $commandResult = 'FAIL'
          & $Log ($name + ' command failed: ' + $_.Exception.Message)
          Add-WapcError -Context $Context -Stage 'ServicesHealthy' -Message ($name + ': ' + $_.Exception.Message)
        }
      }
    } elseif ($Restart -and -not $Context.is_elevated) {
      $commandResult = 'BLOCKED'
    } elseif ($WhatIfPreference) {
      $commandResult = 'SKIPPED'
    }

    Start-Sleep -Milliseconds 200
    $after = $null
    try { $after = (Get-Service $name -ErrorAction Stop).Status } catch { $after = 'Unavailable' }

    $effective = if ($after -eq 'Running') { 'PASS' } else { 'FAIL' }
    [void]$details.Add([ordered]@{
      name             = $name
      command          = $commandResult
      state_before     = [string]$before
      state_after      = [string]$after
      effective_health = $effective
    })
    & $Log ($name + ' command=' + $commandResult + ' final=' + $after + ' health=' + $effective)
  }

  $core = @($details | Where-Object { $_.name -in @('bthserv', 'BthAvctpSvc') })
  $healthy = ($core | Where-Object { $_.effective_health -eq 'PASS' }).Count -eq $core.Count
  return [ordered]@{
    healthy  = $healthy
    status   = if ($healthy) { 'PASS' } elseif (-not $Context.is_elevated) { 'BLOCKED' } else { 'FAIL' }
    services = @($details)
  }
}

Export-ModuleMember -Function @('Test-WapcBluetoothServices')
