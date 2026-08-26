#Requires -Version 5.1
<#
.SYNOPSIS
  Read-only evidence collector for Bluetooth headset → Windows audio path.
.PARAMETER DeviceName
  Friendly name fragment to scope (default: EDIFIER W800BT Pro).
.NOTES
  Does NOT restart services, toggle adapters, or delete pairing.
#>
[CmdletBinding()]
param(
  [string]$DeviceName = 'EDIFIER W800BT Pro'
)

$ErrorActionPreference = 'SilentlyContinue'
$needle = $DeviceName

function Get-ServiceStatus([string]$Name) {
  $s = Get-Service -Name $Name -ErrorAction SilentlyContinue
  if ($s) { return [string]$s.Status }
  return 'Missing'
}

function Get-DriverInfo([string]$InstanceId) {
  if (-not $InstanceId) { return $null }
  $cim = Get-CimInstance Win32_PnPSignedDriver -ErrorAction SilentlyContinue |
    Where-Object { $_.DeviceID -eq $InstanceId } |
    Select-Object -First 1
  if (-not $cim) { return $null }
  return [ordered]@{
    driver_version  = [string]$cim.DriverVersion
    driver_date     = [string]$cim.DriverDate
    driver_provider = [string]$cim.DriverProviderName
    device_name     = [string]$cim.DeviceName
  }
}

# Capability probe (one shot)
$winrtScript = Join-Path $PSScriptRoot '..\Platform\WinRT.ps1'
$capabilities = $null
if (Test-Path $winrtScript) {
  try {
    $capRaw = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $winrtScript 2>$null
    if ($capRaw) { $capabilities = $capRaw | ConvertFrom-Json }
  } catch {
    $capabilities = [ordered]@{
      bluetooth_discovery = $false
      primary_failure = [ordered]@{
        capability = 'bluetooth_discovery'
        available = $false
        reason = 'capability_probe_failed'
        detail = $_.Exception.Message
      }
    }
  }
}

$adapters = @()
Get-PnpDevice -ErrorAction SilentlyContinue |
  Where-Object {
    $_.FriendlyName -and (
      $_.FriendlyName -match 'Bluetooth Adapter' -or
      ($_.Class -eq 'Bluetooth' -and $_.FriendlyName -match 'Adapter|Radio')
    )
  } |
  ForEach-Object {
    $iid = $_.InstanceId
    $props = Get-PnpDeviceProperty -InstanceId $iid -ErrorAction SilentlyContinue
    $problem = ($props | Where-Object KeyName -eq 'DEVPKEY_Device_ProblemCode').Data
    $present = ($props | Where-Object KeyName -eq 'DEVPKEY_Device_IsPresent').Data
    $cim = Get-CimInstance Win32_PnPEntity -ErrorAction SilentlyContinue |
      Where-Object { $_.PNPDeviceID -eq $iid } |
      Select-Object -First 1
    $adapters += [ordered]@{
      name = [string]$_.FriendlyName
      status = [string]$_.Status
      instance_id = [string]$iid
      class = [string]$_.Class
      is_present = [bool]$present
      problem_code = if ($null -ne $problem) { [int]$problem } else { $null }
      config_manager_error = if ($cim) { [string]$cim.ConfigManagerErrorCode } else { $null }
      driver = Get-DriverInfo $iid
    }
  }

$classes = @('Bluetooth', 'MEDIA', 'AudioEndpoint', 'SoftwareComponent', 'System')
$pnp_nodes = @()
foreach ($cls in $classes) {
  Get-PnpDevice -Class $cls -ErrorAction SilentlyContinue |
    Where-Object { $_.FriendlyName -and $_.FriendlyName -match [regex]::Escape($needle) } |
    ForEach-Object {
      $props = Get-PnpDeviceProperty -InstanceId $_.InstanceId -ErrorAction SilentlyContinue
      $present = ($props | Where-Object KeyName -eq 'DEVPKEY_Device_IsPresent').Data
      $addr = ($props | Where-Object KeyName -eq 'DEVPKEY_Bluetooth_DeviceAddress').Data
      $last = ($props | Where-Object KeyName -eq 'DEVPKEY_Bluetooth_LastConnectedTime').Data
      $pnp_nodes += [ordered]@{
        name = [string]$_.FriendlyName
        status = [string]$_.Status
        class = [string]$_.Class
        instance_id = [string]$_.InstanceId
        is_present = [bool]$present
        address = if ($addr) { ([string]$addr).ToLowerInvariant() } else { $null }
        last_connected = if ($last) { [string]$last } else { $null }
      }
    }
}

# Also catch A2DP/HFP profile nodes that include VID/PID under BTHENUM without friendly EDIFIER on every node
$btDevice = $pnp_nodes | Where-Object { $_.class -eq 'Bluetooth' } | Select-Object -First 1
$address = $null
if ($btDevice -and $btDevice.address) { $address = $btDevice.address }

$a2dp_nodes = @()
$media_nodes = @()
$endpoint_nodes = @()
if ($address) {
  $addrToken = $address.ToUpperInvariant()
  Get-PnpDevice -ErrorAction SilentlyContinue |
    Where-Object { $_.InstanceId -match $addrToken -or ($_.FriendlyName -and $_.FriendlyName -match [regex]::Escape($needle)) } |
    ForEach-Object {
      $item = [ordered]@{
        name = [string]$_.FriendlyName
        status = [string]$_.Status
        class = [string]$_.Class
        instance_id = [string]$_.InstanceId
      }
      if ($_.InstanceId -match '0000110B') { $a2dp_nodes += $item }
      if ($_.Class -eq 'MEDIA') { $media_nodes += $item }
      if ($_.Class -eq 'AudioEndpoint') { $endpoint_nodes += $item }
    }
} else {
  $media_nodes = @($pnp_nodes | Where-Object { $_.class -eq 'MEDIA' })
  $endpoint_nodes = @($pnp_nodes | Where-Object { $_.class -eq 'AudioEndpoint' })
  Get-PnpDevice -ErrorAction SilentlyContinue |
    Where-Object { $_.FriendlyName -match [regex]::Escape($needle) -and $_.InstanceId -match '0000110B' } |
    ForEach-Object {
      $a2dp_nodes += [ordered]@{
        name = [string]$_.FriendlyName
        status = [string]$_.Status
        class = [string]$_.Class
        instance_id = [string]$_.InstanceId
      }
    }
}

# Broader endpoint search (Windows often names them "Headphones (EDIFIER...)")
if (-not $endpoint_nodes.Count) {
  Get-PnpDevice -Class AudioEndpoint -ErrorAction SilentlyContinue |
    Where-Object { $_.FriendlyName -match [regex]::Escape($needle) -or $_.FriendlyName -match 'Headphone' } |
    ForEach-Object {
      if ($_.FriendlyName -match [regex]::Escape($needle)) {
        $endpoint_nodes += [ordered]@{
          name = [string]$_.FriendlyName
          status = [string]$_.Status
          class = 'AudioEndpoint'
          instance_id = [string]$_.InstanceId
        }
      }
    }
}

$paired = [bool]$btDevice
$connected = $false
if ($btDevice) {
  $connected = ($btDevice.status -eq 'OK' -and $btDevice.is_present -and $btDevice.last_connected)
  # Presence OK without last_connected can still mean half-paired ghost
  if ($btDevice.status -eq 'OK' -and $btDevice.is_present -and ($media_nodes.Count -gt 0 -or $a2dp_nodes.Count -gt 0)) {
    $connected = $true
  }
}

$primaryAdapter = $adapters | Select-Object -First 1
$adapterEnabled = $false
if ($primaryAdapter) {
  $pc = $primaryAdapter.problem_code
  $adapterEnabled = ($primaryAdapter.status -eq 'OK') -and ($null -eq $pc -or $pc -eq 0)
}

$evidence = [ordered]@{
  timestamp = (Get-Date).ToUniversalTime().ToString('o')
  environment = [ordered]@{
    windows_version = [Environment]::OSVersion.VersionString
    powershell_version = $PSVersionTable.PSVersion.ToString()
    powershell_edition = [string]$PSVersionTable.PSEdition
    device_filter = $DeviceName
  }
  capabilities = $capabilities
  device = [ordered]@{
    name = if ($btDevice) { $btDevice.name } else { $DeviceName }
    paired = $paired
    connected = [bool]$connected
    address = $address
    instance_id = if ($btDevice) { $btDevice.instance_id } else { $null }
    status = if ($btDevice) { $btDevice.status } else { $null }
    last_connected = if ($btDevice) { $btDevice.last_connected } else { $null }
  }
  bluetooth = [ordered]@{
    adapter_present = [bool]$adapters.Count
    adapter_enabled = $adapterEnabled
    adapter_status = if ($primaryAdapter) { $primaryAdapter.status } else { $null }
    adapter_name = if ($primaryAdapter) { $primaryAdapter.name } else { $null }
    adapter_instance_id = if ($primaryAdapter) { $primaryAdapter.instance_id } else { $null }
    adapter_driver = if ($primaryAdapter) { $primaryAdapter.driver } else { $null }
    adapters = $adapters
  }
  pnp = [ordered]@{
    nodes = $pnp_nodes
    a2dp_nodes = $a2dp_nodes
    media_nodes = $media_nodes
    endpoint_nodes = $endpoint_nodes
  }
  audio = [ordered]@{
    media_node_present = [bool]$media_nodes.Count
    a2dp_present = [bool]$a2dp_nodes.Count
    endpoint_present = [bool]$endpoint_nodes.Count
    endpoint_active = [bool](@($endpoint_nodes | Where-Object { $_.status -eq 'OK' }).Count)
    endpoints = $endpoint_nodes
  }
  services = [ordered]@{
    bthserv = Get-ServiceStatus 'bthserv'
    BTAGService = Get-ServiceStatus 'BTAGService'
    BthAvctpSvc = Get-ServiceStatus 'BthAvctpSvc'
    DeviceAssociationService = Get-ServiceStatus 'DeviceAssociationService'
    Audiosrv = Get-ServiceStatus 'Audiosrv'
    AudioEndpointBuilder = Get-ServiceStatus 'AudioEndpointBuilder'
  }
}

$evidence | ConvertTo-Json -Compress -Depth 10
