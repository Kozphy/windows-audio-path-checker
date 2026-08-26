#Requires -Version 5.1
# Thin CLI wrapper around Platform/WinRT.psm1 (ASCII-only).
# Use -JsonOnly when called from Python (stdout must be pure JSON).
param(
  [switch]$JsonOnly
)

$ErrorActionPreference = 'Stop'
$module = Join-Path $PSScriptRoot 'WinRT.psm1'
Import-Module -Name $module -Force
$cap = Get-BluetoothDiscoveryCapability
if (-not $JsonOnly) {
  Write-BluetoothDiscoveryCapabilityReport -Capability $cap
}
# Emit JSON to stdout only (no Write-Host mixing when -JsonOnly).
[Console]::Out.WriteLine(($cap | ConvertTo-Json -Compress -Depth 8))
