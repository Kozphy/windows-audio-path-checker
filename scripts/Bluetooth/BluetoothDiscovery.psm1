#Requires -Version 5.1
# Structured Bluetooth discovery via WinRT (ASCII-only).

$BT_CLASSIC_PROTOCOL = '{E0CBF06C-CD8B-4647-BB8A-263B43F0F974}'
$BT_BLE_PROTOCOL = '{BB7BB05E-5972-42B5-94FC-76EAA7084D49}'

function Initialize-WapcWinRtBluetoothTypes {
  Add-Type -AssemblyName System.Runtime.WindowsRuntime -ErrorAction Stop | Out-Null
  $null = [Windows.Devices.Enumeration.DeviceInformation, Windows.Devices.Enumeration, ContentType = WindowsRuntime]
  $null = [Windows.Devices.Enumeration.DeviceInformationCollection, Windows.Devices.Enumeration, ContentType = WindowsRuntime]
  $null = [Windows.Devices.Enumeration.DeviceInformationKind, Windows.Devices.Enumeration, ContentType = WindowsRuntime]
  $null = [Windows.Devices.Enumeration.DevicePairingResult, Windows.Devices.Enumeration, ContentType = WindowsRuntime]
  $null = [Windows.Devices.Bluetooth.BluetoothDevice, Windows.Devices.Bluetooth, ContentType = WindowsRuntime]

  $script:WapcAsTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
  } | Select-Object -First 1
  if (-not $script:WapcAsTask) { throw 'AsTask helper missing' }
}

function Invoke-WapcWinRt {
  param($AsyncOp, [Type]$ResultType, [int]$TimeoutMs = 15000)
  $task = $script:WapcAsTask.MakeGenericMethod($ResultType).Invoke($null, @($AsyncOp))
  if (-not $task.Wait($TimeoutMs)) { return $null }
  if ($task.IsFaulted) { throw $task.Exception.GetBaseException() }
  return $task.Result
}

function New-WapcStringList {
  param([string[]]$Items)
  $list = New-Object 'System.Collections.Generic.List[string]'
  foreach ($item in $Items) {
    [void]$list.Add([string]$item)
  }
  return $list
}

function Invoke-WapcWinRtFindAll {
  <#
  .SYNOPSIS
    Typed WinRT FindAllAsync wrapper. Returns success/error separately from empty results.
  #>
  param(
    [Parameter(Mandatory)][string]$Aqs,
    [string[]]$AdditionalProperties = @(),
    $Kind = $null,
    [int]$TimeoutMs = 12000
  )

  $collType = [Windows.Devices.Enumeration.DeviceInformationCollection]
  try {
    if ($AdditionalProperties.Count -eq 0 -and $null -eq $Kind) {
      $asyncOp = [Windows.Devices.Enumeration.DeviceInformation]::FindAllAsync([string]$Aqs)
    } elseif ($null -ne $Kind) {
      $propList = New-WapcStringList -Items $AdditionalProperties
      $method = [Windows.Devices.Enumeration.DeviceInformation].GetMethods() | Where-Object {
        if ($_.Name -ne 'FindAllAsync') { return $false }
        $params = $_.GetParameters()
        if ($params.Count -ne 3) { return $false }
        return $params[2].ParameterType.Name -eq 'DeviceInformationKind'
      } | Select-Object -First 1
      if (-not $method) {
        throw 'FindAllAsync(aqs, IEnumerable<string>, DeviceInformationKind) overload not found'
      }
      $kindValue = [Windows.Devices.Enumeration.DeviceInformationKind]$Kind
      $asyncOp = $method.Invoke($null, @([string]$Aqs, $propList, $kindValue))
    } else {
      $propList = New-WapcStringList -Items $AdditionalProperties
      $method = [Windows.Devices.Enumeration.DeviceInformation].GetMethods() | Where-Object {
        if ($_.Name -ne 'FindAllAsync') { return $false }
        $params = $_.GetParameters()
        if ($params.Count -ne 2) { return $false }
        return $params[1].ParameterType.IsGenericType
      } | Select-Object -First 1
      if (-not $method) {
        throw 'FindAllAsync(aqs, IEnumerable<string>) overload not found'
      }
      $asyncOp = $method.Invoke($null, @([string]$Aqs, $propList))
    }

    $coll = Invoke-WapcWinRt $asyncOp $collType $TimeoutMs
    $count = 0
    if ($coll) { $count = [int]$coll.Count }
    return [ordered]@{
      success    = $true
      error      = $null
      count      = $count
      collection = $coll
    }
  } catch {
    return [ordered]@{
      success    = $false
      error      = $_.Exception.Message
      count      = 0
      collection = $null
    }
  }
}

function Get-WapcDeviceProperty {
  param($DeviceInformation, [string]$Key)
  try {
    if ($DeviceInformation.Properties.ContainsKey($Key)) {
      return [string]$DeviceInformation.Properties[$Key]
    }
  } catch { }
  return $null
}

function Test-WapcProtocolKind {
  param([string]$ProtocolId)
  $p = if ($ProtocolId) { $ProtocolId.ToUpperInvariant() } else { '' }
  if ($p -match 'BB7BB05E') { return 'BLE' }
  if ($p -match 'E0CBF06C') { return 'Bluetooth' }
  return 'Unknown'
}

function ConvertTo-WapcBluetoothCandidate {
  param(
    $DeviceInformation,
    [string]$SelectorName,
    [bool]$EnumerationSucceeded = $true,
    [switch]$Diagnostics
  )
  $kind = [string]$DeviceInformation.Kind
  $protocol = Get-WapcDeviceProperty $DeviceInformation 'System.Devices.Aep.ProtocolId'
  $addr = Get-WapcDeviceProperty $DeviceInformation 'System.Devices.Aep.DeviceAddress'
  if (-not $addr) {
    $addr = Get-WapcDeviceProperty $DeviceInformation 'System.Devices.Aep.Bluetooth.Address'
  }
  $container = Get-WapcDeviceProperty $DeviceInformation 'System.Devices.Aep.ContainerId'
  $protoKind = Test-WapcProtocolKind $protocol

  $props = @{}
  if ($Diagnostics) {
    try {
      foreach ($k in $DeviceInformation.Properties.Keys) {
        $props[[string]$k] = [string]$DeviceInformation.Properties[$k]
      }
    } catch { }
  }

  $canPair = [bool]$DeviceInformation.Pairing.CanPair
  $isPaired = [bool]$DeviceInformation.Pairing.IsPaired
  $pairability = 'UNKNOWN'
  if ($EnumerationSucceeded) {
    if ($canPair -and -not $isPaired) { $pairability = 'PAIRABLE' }
    elseif (-not $canPair -and -not $isPaired) { $pairability = 'NOT_PAIRABLE' }
    elseif ($isPaired) { $pairability = 'NOT_PAIRABLE' }
  }

  return [ordered]@{
    name                    = [string]$DeviceInformation.Name
    id                      = [string]$DeviceInformation.Id
    kind                    = $kind
    selector                = $SelectorName
    is_paired               = $isPaired
    can_pair                = $canPair
    pairability             = $pairability
    protocol_id             = $protocol
    aep_protocol_id         = $protocol
    device_address          = if ($addr) { ($addr -replace '[^0-9a-fA-F]', '').ToLowerInvariant() } else { $null }
    address                 = if ($addr) { ($addr -replace '[^0-9a-fA-F]', '').ToLowerInvariant() } else { $null }
    container_id            = $container
    is_classic              = ($protoKind -eq 'Bluetooth')
    is_ble                  = ($protoKind -eq 'BLE')
    enumeration_succeeded   = $EnumerationSucceeded
    stale                   = $false
    properties              = $props
    device_ref              = $DeviceInformation
  }
}

function Test-WapcTargetNameMatch {
  param([string]$Name, [string[]]$Patterns)
  foreach ($p in $Patterns) {
    if ($Name -match [regex]::Escape($p)) { return $true }
  }
  return $false
}

function Get-WapcBluetoothSelectors {
  Initialize-WapcWinRtBluetoothTypes
  return @(
    @{ Name = 'ClassicUnpaired'; Aqs = [Windows.Devices.Bluetooth.BluetoothDevice]::GetDeviceSelectorFromPairingState($false); Classic = $true },
    @{ Name = 'ClassicPaired'; Aqs = [Windows.Devices.Bluetooth.BluetoothDevice]::GetDeviceSelectorFromPairingState($true); Classic = $true },
    @{ Name = 'ClassicAll'; Aqs = [Windows.Devices.Bluetooth.BluetoothDevice]::GetDeviceSelector(); Classic = $true },
    @{ Name = 'AepClassicUnpaired'; Aqs = ('System.Devices.Aep.ProtocolId:="' + $BT_CLASSIC_PROTOCOL + '" AND System.Devices.Aep.IsPaired:=false'); Classic = $true },
    @{ Name = 'AepClassicPaired'; Aqs = ('System.Devices.Aep.ProtocolId:="' + $BT_CLASSIC_PROTOCOL + '" AND System.Devices.Aep.IsPaired:=true'); Classic = $true },
    @{ Name = 'AepBluetooth'; Aqs = ('System.Devices.Aep.ProtocolId:="' + $BT_CLASSIC_PROTOCOL + '"'); Classic = $true },
    @{ Name = 'AepBle'; Aqs = ('System.Devices.Aep.ProtocolId:="' + $BT_BLE_PROTOCOL + '"'); Classic = $false },
    @{ Name = 'GenericAssociationEndpoint'; Aqs = 'System.Devices.Aep.ProtocolId:*'; Classic = $false }
  )
}

function Merge-WapcBluetoothCandidates {
  param([System.Collections.ArrayList]$Candidates)
  $byKey = @{}
  $merged = New-Object System.Collections.ArrayList
  foreach ($c in $Candidates) {
    $addr = [string]$c.device_address
    $key = if ($addr) { $addr } elseif ($c.container_id) { [string]$c.container_id } else { [string]$c.id }
    if (-not $byKey.ContainsKey($key)) {
      $byKey[$key] = $true
      [void]$merged.Add($c)
    } else {
      foreach ($existing in $merged) {
        if (($existing.device_address -and $existing.device_address -eq $addr) -or
            ($existing.id -eq $c.id)) {
          if (-not $existing.selectors) { $existing.selectors = @([string]$existing.selector) }
          $existing.selectors += [string]$c.selector
          if ($c.enumeration_succeeded -and $c.pairability -eq 'PAIRABLE') {
            $existing.can_pair = $true
            $existing.pairability = 'PAIRABLE'
          }
          break
        }
      }
    }
  }
  return ,@($merged.ToArray())
}

function Get-WapcBluetoothCandidates {
  param(
    [string[]]$NamePatterns,
    [string]$ExpectedAddress = '',
    [switch]$Diagnostics,
    [scriptblock]$Log = { param($m) Write-Host $m }
  )

  Initialize-WapcWinRtBluetoothTypes
  $kindAssoc = [Windows.Devices.Enumeration.DeviceInformationKind]::AssociationEndpoint
  $aepProps = [string[]]@(
    'System.Devices.Aep.DeviceAddress',
    'System.Devices.Aep.IsPaired',
    'System.Devices.Aep.CanPair',
    'System.Devices.Aep.ProtocolId',
    'System.Devices.Aep.ContainerId',
    'System.Devices.Aep.Bluetooth.Address'
  )

  $selectors = Get-WapcBluetoothSelectors
  $selectorReports = New-Object System.Collections.ArrayList
  $raw = New-Object System.Collections.ArrayList
  $seenIds = @{}

  foreach ($sel in $selectors) {
    foreach ($mode in @(
      @{ Label = 'Default'; Kind = $null },
      @{ Label = 'AssociationEndpoint'; Kind = $kindAssoc },
      @{ Label = 'AepProps'; Kind = $null; PropsOnly = $true }
    )) {
      if ($mode.PropsOnly -and -not ($sel.Name -like 'Aep*' -or $sel.Name -eq 'GenericAssociationEndpoint')) {
        continue
      }
      $selectorLabel = $sel.Name + '+' + $mode.Label
      $result = if ($mode.PropsOnly) {
        Invoke-WapcWinRtFindAll -Aqs $sel.Aqs -AdditionalProperties $aepProps -TimeoutMs 12000
      } elseif ($null -ne $mode.Kind) {
        Invoke-WapcWinRtFindAll -Aqs $sel.Aqs -AdditionalProperties $aepProps -Kind $mode.Kind -TimeoutMs 12000
      } else {
        Invoke-WapcWinRtFindAll -Aqs $sel.Aqs -TimeoutMs 12000
      }

      $matched = 0
      if ($result.success -and $result.collection) {
        foreach ($dev in $result.collection) {
          if (-not (Test-WapcTargetNameMatch $dev.Name $NamePatterns)) { continue }
          if ($seenIds.ContainsKey($dev.Id)) { continue }
          $seenIds[$dev.Id] = $true
          [void]$raw.Add((ConvertTo-WapcBluetoothCandidate $dev $selectorLabel -EnumerationSucceeded $true -Diagnostics:$Diagnostics))
          $matched++
        }
      }

      [void]$selectorReports.Add([ordered]@{
        selector   = $selectorLabel
        classic    = [bool]$sel.Classic
        success    = [bool]$result.success
        error      = $result.error
        total      = [int]$result.count
        matched    = $matched
      })
      if (-not $result.success) {
        & $Log ('Enumeration ERROR ' + $selectorLabel + ': ' + $result.error)
      }
    }
  }

  $candidates = Merge-WapcBluetoothCandidates -Candidates $raw
  $classicReports = @($selectorReports | Where-Object { $_.classic })
  $aepReports = @($selectorReports | Where-Object { $_.selector -match 'AssociationEndpoint|AepProps' })
  $classicSucceeded = ($classicReports | Where-Object { $_.success }).Count -gt 0
  $classicAllFailed = ($classicReports.Count -gt 0) -and (($classicReports | Where-Object { $_.success }).Count -eq 0)
  $aepSucceeded = ($aepReports | Where-Object { $_.success }).Count -gt 0
  $aepAllFailed = ($aepReports.Count -gt 0) -and (($aepReports | Where-Object { $_.success }).Count -eq 0)
  $targetDiscovered = ($candidates.Count -gt 0)

  $enumeration = [ordered]@{
    selectors                      = @($selectorReports)
    classic_enumeration_succeeded  = $classicSucceeded
    classic_enumeration_all_failed = $classicAllFailed
    aep_enumeration_succeeded      = $aepSucceeded
    aep_enumeration_all_failed     = $aepAllFailed
    target_discovered              = $targetDiscovered
    candidate_count                = $candidates.Count
  }

  return [ordered]@{
    candidates  = $candidates
    enumeration = $enumeration
  }
}

function Write-BluetoothCandidateLog {
  param([array]$Candidates, [int]$Index, [switch]$VerboseLog)
  $c = $Candidates[$Index]
  Write-Host ('Candidate #{0}' -f ($Index + 1))
  Write-Host ('  Name          : {0}' -f $c.name)
  Write-Host ('  Id            : {0}' -f $c.id)
  Write-Host ('  Kind          : {0}' -f $c.kind)
  Write-Host ('  Pairability   : {0}' -f $c.pairability)
  Write-Host ('  IsPaired      : {0}' -f $c.is_paired)
  Write-Host ('  CanPair       : {0}' -f $c.can_pair)
  Write-Host ('  ProtocolId    : {0}' -f $c.protocol_id)
  Write-Host ('  DeviceAddress : {0}' -f $c.device_address)
  Write-Host ('  Selector      : {0}' -f $c.selector)
  Write-Host ('  EnumSucceeded : {0}' -f $c.enumeration_succeeded)
}

Export-ModuleMember -Function @(
  'Initialize-WapcWinRtBluetoothTypes',
  'Invoke-WapcWinRt',
  'Invoke-WapcWinRtFindAll',
  'Get-WapcBluetoothSelectors',
  'Get-WapcBluetoothCandidates',
  'Merge-WapcBluetoothCandidates',
  'ConvertTo-WapcBluetoothCandidate',
  'Write-BluetoothCandidateLog'
)
