#Requires -Version 5.1
# Self-contained regression checks for WapcBluetoothCleanup (no Pester required).
$ErrorActionPreference = 'Stop'
$root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
if (-not (Test-Path (Join-Path $root 'scripts\Bluetooth\WapcBluetoothCleanup.psm1'))) {
  $root = Split-Path $PSScriptRoot -Parent
}
$bt = Join-Path $root 'scripts\Bluetooth'
Import-Module (Join-Path $bt 'WapcBluetoothIdentity.psm1') -Force -Global
Import-Module (Join-Path $bt 'WapcBluetoothCleanup.psm1') -Force -Global

$failed = 0
function Assert-True([bool]$Cond, [string]$Msg) {
  if (-not $Cond) {
    Write-Host ("FAIL: " + $Msg)
    $script:failed++
  } else {
    Write-Host ("PASS: " + $Msg)
  }
}

# Case C / regression: missing InstanceId must never call pnputil
$missing = [pscustomobject]@{ FriendlyName = 'EDIFIER W800BT Pro'; Class = 'Bluetooth'; Status = 'OK' }
$id = Get-WapcPnpInstanceId -Device $missing
Assert-True ($null -eq $id) 'missing InstanceId extracts as null'

$removal = Remove-WapcPnpDeviceSafe -Device $missing -ExpectedAddress 'c8247887e57c' -Log { param($m) }
Assert-True ($removal.Reason -eq 'MISSING_INSTANCE_ID') 'MISSING_INSTANCE_ID classification'
Assert-True (-not $removal.Success) 'removal not successful without InstanceId'

# Case A: valid InstanceId extracted
$ok = [pscustomobject]@{
  FriendlyName = 'EDIFIER W800BT Pro'
  Class = 'Bluetooth'
  Status = 'OK'
  InstanceId = 'BTHENUM\Dev_C8247887E57C\a&19b543a3&0&BluetoothDevice_C8247887E57C'
}
Assert-True ((Get-WapcPnpInstanceId -Device $ok) -match 'C8247887E57C') 'extracts Dev_C8247887E57C InstanceId'

# Case D: usage output detection
Assert-True (Test-WapcPnputilUsageOutput -Output 'Microsoft PnP Utility PNPUTIL [/add-driver <Path>]') 'detects pnputil usage output'

# Case I: address normalization
Assert-True ((ConvertTo-WapcNormalizedBluetoothAddress 'C8:24:78:87:E5:7C') -eq 'c8247887e57c') 'normalizes colon MAC'
Assert-True ((ConvertTo-WapcNormalizedBluetoothAddress 'c8-24-78-87-e5-7c') -eq 'c8247887e57c') 'normalizes dash MAC'

# Nested empty array must not look like a device
$nestedEmpty = ,@()
Assert-True ($null -eq (Get-WapcPnpInstanceId -Device $nestedEmpty)) 'nested empty array => null InstanceId'

# WhatIf with valid id must not claim MISSING
$whatIf = Remove-WapcPnpDeviceSafe -Device $ok -ExpectedAddress 'c8247887e57c' -WhatIfPreference -Log { param($m) }
Assert-True ($whatIf.Reason -eq 'WHATIF_SKIPPED') 'WhatIf skips destructive pnputil'
Assert-True ($whatIf.InstanceId -match 'C8247887E57C') 'WhatIf still has InstanceId'

if ($failed -gt 0) {
  Write-Host ("RESULT: FAILED ($failed)")
  exit 1
}
Write-Host 'RESULT: PASSED'
exit 0
