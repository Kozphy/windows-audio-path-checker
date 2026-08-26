#Requires -Version 5.1
<#
.SYNOPSIS
  WinRT / Bluetooth discovery capability helpers for Windows PowerShell 5.1+.
.NOTES
  Never assume bare [Windows.Devices.Enumeration.DeviceInformation] resolves.
  Call Get-BluetoothDiscoveryCapability once; do not retry capability failures.
#>

function New-WapcCapabilityResult {
  param(
    [string]$Capability,
    [bool]$Available,
    [string]$Reason = '',
    [hashtable]$Extra = @{}
  )
  $o = [ordered]@{
    capability         = $Capability
    available          = $Available
    reason             = $Reason
    powershell_version = $PSVersionTable.PSVersion.ToString()
    powershell_edition = [string]$PSVersionTable.PSEdition
    clr_version        = [Environment]::Version.ToString()
    is_64bit           = [Environment]::Is64BitProcess
  }
  foreach ($k in $Extra.Keys) { $o[$k] = $Extra[$k] }
  return $o
}

function Test-WapcWinRTAssembly {
  try {
    Add-Type -AssemblyName System.Runtime.WindowsRuntime -ErrorAction Stop | Out-Null
    return $true
  } catch {
    return $false
  }
}

function Test-WapcDeviceInformationSupport {
  if (-not (Test-WapcWinRTAssembly)) { return $false }
  try {
    $null = [Windows.Devices.Enumeration.DeviceInformation, Windows.Devices.Enumeration, ContentType = WindowsRuntime]
    $t = [Windows.Devices.Enumeration.DeviceInformation]
    return ($null -ne $t)
  } catch {
    return $false
  }
}

function Get-BluetoothDiscoveryCapability {
  <#
  .SYNOPSIS
    One-shot structured capability probe. Never loops.
  #>
  $result = [ordered]@{
    timestamp                      = (Get-Date).ToUniversalTime().ToString('o')
    available                      = $false
    winrt_available                = $false
    device_information_available   = $false
    bluetooth_discovery_available  = $false
    reason                         = $null
    powershell_version             = $PSVersionTable.PSVersion.ToString()
    powershell_edition             = [string]$PSVersionTable.PSEdition
    capabilities                   = @()
    detail                         = $null
  }

  $winrtOk = Test-WapcWinRTAssembly
  $result.winrt_available = $winrtOk
  $result.capabilities += ,(New-WapcCapabilityResult -Capability 'system_runtime_windowsruntime' -Available $winrtOk -Reason $(if ($winrtOk) { '' } else { 'assembly_load_failed' }))

  $diOk = $false
  $diDetail = ''
  if ($winrtOk) {
    try {
      $null = [Windows.Devices.Enumeration.DeviceInformation, Windows.Devices.Enumeration, ContentType = WindowsRuntime]
      $t = [Windows.Devices.Enumeration.DeviceInformation]
      if ($null -eq $t) { throw 'Type resolved to null' }
      $diOk = $true
      $diDetail = $t.FullName
    } catch {
      $diDetail = $_.Exception.Message
    }
  } else {
    $diDetail = 'winrt_assembly_unavailable'
  }
  $result.device_information_available = $diOk
  $result.capabilities += ,(New-WapcCapabilityResult -Capability 'winrt_device_information' -Available $diOk -Reason $(if ($diOk) { '' } else { 'winrt_type_unavailable' }) -Extra @{ type_name = $diDetail })

  if (-not $diOk) {
    $result.reason = 'winrt_type_unavailable'
    $result.detail = $diDetail
    $result.available = $false
    return [pscustomobject]$result
  }

  try {
    $null = [Windows.Devices.Enumeration.DeviceInformationCollection, Windows.Devices.Enumeration, ContentType = WindowsRuntime]
    $null = [Windows.Devices.Bluetooth.BluetoothDevice, Windows.Devices.Bluetooth, ContentType = WindowsRuntime]
    $asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
      $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and
      $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    } | Select-Object -First 1
    if (-not $asTask) { throw 'AsTask helper missing' }
    $sel = [Windows.Devices.Bluetooth.BluetoothDevice]::GetDeviceSelector()
    $op = [Windows.Devices.Enumeration.DeviceInformation]::FindAllAsync($sel)
    $task = $asTask.MakeGenericMethod([Windows.Devices.Enumeration.DeviceInformationCollection]).Invoke($null, @($op))
    if (-not $task.Wait(8000)) { throw 'FindAllAsync timed out' }
    if ($task.IsFaulted) { throw $task.Exception.GetBaseException() }
    $result.bluetooth_discovery_available = $true
    $result.available = $true
    $result.reason = $null
    $result.detail = "device_count=$([int]$task.Result.Count)"
    $result.capabilities += ,(New-WapcCapabilityResult -Capability 'bluetooth_discovery' -Available $true -Extra @{ device_count = [int]$task.Result.Count; selector = [string]$sel })
  } catch {
    $result.available = $false
    $result.bluetooth_discovery_available = $false
    $result.reason = 'bluetooth_discovery_api_unusable'
    $result.detail = $_.Exception.Message
    $result.capabilities += ,(New-WapcCapabilityResult -Capability 'bluetooth_discovery' -Available $false -Reason 'bluetooth_discovery_api_unusable' -Extra @{ detail = $_.Exception.Message })
  }

  return [pscustomobject]$result
}

function Write-BluetoothDiscoveryCapabilityReport {
  param([Parameter(Mandatory)]$Capability)
  Write-Host ''
  Write-Host '=== DISCOVERY CAPABILITY CHECK ==='
  Write-Host ("PowerShell                {0} ({1})" -f $Capability.powershell_version, $Capability.powershell_edition)
  Write-Host ("WinRT                     {0}" -f $(if ($Capability.winrt_available) { 'AVAILABLE' } else { 'UNAVAILABLE' }))
  Write-Host ("DeviceInformation         {0}" -f $(if ($Capability.device_information_available) { 'AVAILABLE' } else { 'UNAVAILABLE' }))
  Write-Host ("Bluetooth discovery       {0}" -f $(if ($Capability.available) { 'AVAILABLE' } else { 'UNAVAILABLE' }))
  if (-not $Capability.available) {
    Write-Host ''
    Write-Host 'Reason:'
    Write-Host ("  {0}" -f $(if ($Capability.reason) { $Capability.reason } else { 'unknown' }))
    if ($Capability.detail) {
      Write-Host ("  {0}" -f $Capability.detail)
    }
    Write-Host ''
    Write-Host 'AUTO-PAIR skipped.'
  }
  Write-Host ''
}

Export-ModuleMember -Function @(
  'Test-WapcWinRTAssembly',
  'Test-WapcDeviceInformationSupport',
  'Get-BluetoothDiscoveryCapability',
  'Write-BluetoothDiscoveryCapabilityReport'
)
