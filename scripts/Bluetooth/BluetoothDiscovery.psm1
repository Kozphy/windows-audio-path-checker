#Requires -Version 5.1
# Structured Bluetooth discovery via WinRT (ASCII-only).

$BT_CLASSIC_PROTOCOL = '{E0CBF06C-CD8B-4647-BB8A-263B43F0F974}'
$BT_BLE_PROTOCOL = '{BB7BB05E-5972-42B5-94FC-76EAA7084D49}'

Import-Module (Join-Path $PSScriptRoot 'WapcBluetoothIdentity.psm1') -Force -Global

function Initialize-WapcWinRtBluetoothTypes {
  Add-Type -AssemblyName System.Runtime.WindowsRuntime -ErrorAction Stop | Out-Null
  $null = [Windows.Devices.Enumeration.DeviceInformation, Windows.Devices.Enumeration, ContentType = WindowsRuntime]
  $null = [Windows.Devices.Enumeration.DeviceInformationCollection, Windows.Devices.Enumeration, ContentType = WindowsRuntime]
  $null = [Windows.Devices.Enumeration.DeviceInformationKind, Windows.Devices.Enumeration, ContentType = WindowsRuntime]
  $null = [Windows.Devices.Enumeration.DevicePairingResult, Windows.Devices.Enumeration, ContentType = WindowsRuntime]
  $null = [Windows.Devices.Enumeration.DeviceUnpairingResult, Windows.Devices.Enumeration, ContentType = WindowsRuntime]
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
  if ($null -ne $Items) {
    foreach ($item in @($Items)) {
      if ($null -ne $item -and "$item" -ne '') {
        [void]$list.Add([string]$item)
      }
    }
  }
  # Comma prevents PowerShell from unrolling the List into loose strings.
  return ,$list
}

function Get-WapcStringListBase {
  param($MaybeList)
  if ($null -eq $MaybeList) { return $null }
  if ($MaybeList -is [System.Collections.Generic.List[string]]) { return $MaybeList }
  if ($MaybeList -is [System.Management.Automation.PSObject]) {
    $base = $MaybeList.psobject.BaseObject
    if ($base -is [System.Collections.Generic.List[string]]) { return $base }
  }
  # Rebuild from whatever enumerable we got.
  $list = New-Object 'System.Collections.Generic.List[string]'
  foreach ($item in @($MaybeList)) {
    if ($null -ne $item) { [void]$list.Add([string]$item) }
  }
  return ,$list
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
    if ((-not $AdditionalProperties -or $AdditionalProperties.Count -eq 0) -and $null -eq $Kind) {
      $asyncOp = [Windows.Devices.Enumeration.DeviceInformation]::FindAllAsync([string]$Aqs)
    } elseif ($null -ne $Kind) {
      # Build List inline so PowerShell cannot wrap/unroll it via function return.
      $propList = New-Object 'System.Collections.Generic.List[string]'
      foreach ($p in @($AdditionalProperties)) {
        if ($null -ne $p -and "$p" -ne '') { [void]$propList.Add([string]$p) }
      }
      $method = [Windows.Devices.Enumeration.DeviceInformation].GetMethods() | Where-Object {
        if ($_.Name -ne 'FindAllAsync') { return $false }
        $params = $_.GetParameters()
        if ($params.Count -ne 3) { return $false }
        return $params[2].ParameterType.Name -eq 'DeviceInformationKind'
      } | Select-Object -First 1
      if (-not $method) {
        throw 'FindAllAsync(aqs, IEnumerable<string>, DeviceInformationKind) overload not found'
      }
      if ($method -is [System.Management.Automation.PSObject]) {
        $method = $method.psobject.BaseObject
      }
      $kindValue = [Windows.Devices.Enumeration.DeviceInformationKind]$Kind
      $invokeArgs = [object[]]::new(3)
      $invokeArgs.SetValue([string]$Aqs, 0)
      $invokeArgs.SetValue($propList, 1)
      $invokeArgs.SetValue($kindValue, 2)
      $asyncOp = $method.Invoke($null, $invokeArgs)
    } else {
      $propList = New-Object 'System.Collections.Generic.List[string]'
      foreach ($p in @($AdditionalProperties)) {
        if ($null -ne $p -and "$p" -ne '') { [void]$propList.Add([string]$p) }
      }
      $method = [Windows.Devices.Enumeration.DeviceInformation].GetMethods() | Where-Object {
        if ($_.Name -ne 'FindAllAsync') { return $false }
        $params = $_.GetParameters()
        if ($params.Count -ne 2) { return $false }
        return $params[1].ParameterType.IsGenericType
      } | Select-Object -First 1
      if (-not $method) {
        throw 'FindAllAsync(aqs, IEnumerable<string>) overload not found'
      }
      if ($method -is [System.Management.Automation.PSObject]) {
        $method = $method.psobject.BaseObject
      }
      $invokeArgs = [object[]]::new(2)
      $invokeArgs.SetValue([string]$Aqs, 0)
      $invokeArgs.SetValue($propList, 1)
      $asyncOp = $method.Invoke($null, $invokeArgs)
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

function Update-WapcBluetoothCandidatePairing {
  <#
  .SYNOPSIS
    FindAllAsync often returns stale Pairing.CanPair/IsPaired. CreateFromIdAsync refreshes them.
  #>
  param($Candidate)
  if (-not $Candidate -or -not $Candidate.id) { return $Candidate }
  try {
    Initialize-WapcWinRtBluetoothTypes
    $op = [Windows.Devices.Enumeration.DeviceInformation]::CreateFromIdAsync([string]$Candidate.id)
    $fresh = Invoke-WapcWinRt $op ([Windows.Devices.Enumeration.DeviceInformation]) 8000
    if (-not $fresh) { return $Candidate }
    $Candidate.device_ref = $fresh
    $Candidate.can_pair = [bool]$fresh.Pairing.CanPair
    $Candidate.is_paired = [bool]$fresh.Pairing.IsPaired
    if ($Candidate.can_pair -and -not $Candidate.is_paired) {
      $Candidate.pairability = 'PAIRABLE'
    } elseif ($Candidate.is_paired) {
      $Candidate.pairability = 'NOT_PAIRABLE'
    } elseif (-not $Candidate.can_pair) {
      $Candidate.pairability = 'NOT_PAIRABLE'
    }
    if ($Candidate.selector -match '^Classic' -or $Candidate.selector -match '^AepClassic') {
      $Candidate.is_classic = $true
    }
  } catch { }
  return $Candidate
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
  if (-not $addr) {
    # Classic DeviceInformation.Id often embeds MAC: Bluetooth#BluetoothAA:BB:...-CC:DD:EE:FF:00:11
    $id = [string]$DeviceInformation.Id
    if ($id -match '([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$') {
      $addr = $Matches[0]
    } elseif ($id -match 'BluetoothDevice_([0-9A-Fa-f]{12})') {
      $addr = $Matches[1]
    }
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
    @{ Name = 'AepBle'; Aqs = ('System.Devices.Aep.ProtocolId:="' + $BT_BLE_PROTOCOL + '"'); Classic = $false }
    # Generic ProtocolId:* AQS is invalid on this host (E_INVALIDARG) — omit.
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
  # Note: System.Devices.Aep.Bluetooth.Address is NOT a valid property key and
  # causes FindAllAsync to fail with "Property key syntax error" for the whole list.
  $aepProps = [string[]]@(
    'System.Devices.Aep.DeviceAddress',
    'System.Devices.Aep.IsPaired',
    'System.Devices.Aep.CanPair',
    'System.Devices.Aep.IsConnected',
    'System.Devices.Aep.ProtocolId',
    'System.Devices.Aep.ContainerId'
  )

  $selectors = Get-WapcBluetoothSelectors
  $selectorReports = New-Object System.Collections.ArrayList
  $raw = New-Object System.Collections.ArrayList
  $seenIds = @{}

  foreach ($sel in $selectors) {
    # Classic BluetoothDevice AQS rejects AssociationEndpoint + AEP property keys
    # ("Property key syntax error"). Use Default for Classic; AEP kinds for Aep*.
    $modes = @(
      @{ Label = 'Default'; Kind = $null }
    )
    if ($sel.Name -like 'Aep*' -or $sel.Name -eq 'GenericAssociationEndpoint') {
      $modes += @(
        @{ Label = 'AssociationEndpoint'; Kind = $kindAssoc },
        @{ Label = 'AepProps'; Kind = $null; PropsOnly = $true }
      )
    }
    foreach ($mode in $modes) {
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
        $expectedAddr = if ($ExpectedAddress) {
          ($ExpectedAddress -replace '[^0-9a-fA-F]', '').ToLowerInvariant()
        } else { '' }
        if ($expectedAddr.Length -gt 12) {
          $expectedAddr = $expectedAddr.Substring($expectedAddr.Length - 12)
        }
        foreach ($dev in $result.collection) {
          $nameMatch = Test-WapcTargetNameMatch $dev.Name $NamePatterns
          $addrMatch = $false
          if ($expectedAddr) {
            $idAddr = ''
            $id = [string]$dev.Id
            if ($id -match '([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$') {
              $idAddr = ($Matches[0] -replace '[^0-9a-fA-F]', '').ToLowerInvariant()
            } elseif ($id -match 'BluetoothDevice_([0-9A-Fa-f]{12})') {
              $idAddr = $Matches[1].ToLowerInvariant()
            }
            $addrMatch = ($idAddr -and ($idAddr -eq $expectedAddr))
          }
          # Name is a discovery hint; address match is authoritative when known.
          if (-not $nameMatch -and -not $addrMatch) { continue }
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
  $refreshed = New-Object System.Collections.ArrayList
  foreach ($c in @($candidates)) {
    [void]$refreshed.Add((Update-WapcBluetoothCandidatePairing $c))
  }
  $candidates = @($refreshed.ToArray())

  $annotated = New-Object System.Collections.ArrayList
  $acceptedCount = 0
  $targetNameHint = if ($NamePatterns -and $NamePatterns.Count -gt 0) { [string]$NamePatterns[0] } else { '' }
  foreach ($c in $candidates) {
    $c2 = Add-WapcCandidateIdentityAnnotation -Candidate $c -TargetName $targetNameHint `
      -TargetAddress $ExpectedAddress
    if ($c2.disposition -eq 'ACCEPTED') { $acceptedCount++ }
    [void]$annotated.Add($c2)
  }
  $candidates = @($annotated.ToArray())

  $classicReports = @($selectorReports | Where-Object { $_.classic })
  $aepReports = @($selectorReports | Where-Object { $_.selector -match 'AssociationEndpoint|AepProps' })
  $classicSucceeded = ($classicReports | Where-Object { $_.success }).Count -gt 0
  $classicAllFailed = ($classicReports.Count -gt 0) -and (($classicReports | Where-Object { $_.success }).Count -eq 0)
  $aepSucceeded = ($aepReports | Where-Object { $_.success }).Count -gt 0
  $aepAllFailed = ($aepReports.Count -gt 0) -and (($aepReports | Where-Object { $_.success }).Count -eq 0)
  # Exact target only — sibling brand devices must not set target_discovered.
  $targetDiscovered = ($acceptedCount -gt 0)

  $enumeration = [ordered]@{
    selectors                      = @($selectorReports)
    classic_enumeration_succeeded  = $classicSucceeded
    classic_enumeration_all_failed = $classicAllFailed
    aep_enumeration_succeeded      = $aepSucceeded
    aep_enumeration_all_failed     = $aepAllFailed
    target_discovered              = $targetDiscovered
    exact_target_discovered        = $targetDiscovered
    any_bluetooth_device_discovered = ($candidates.Count -gt 0)
    candidate_count                = $candidates.Count
    accepted_count                 = $acceptedCount
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
  if ($null -ne $c.identity_matched) {
    Write-Host ('  IdentityMatch : {0}' -f $c.identity_matched)
    Write-Host ('  Disposition   : {0}' -f $c.disposition)
    if ($c.rejection_reason) {
      Write-Host ('  RejectReason  : {0}' -f $c.rejection_reason)
    }
  }
}

Export-ModuleMember -Function @(
  'Initialize-WapcWinRtBluetoothTypes',
  'Invoke-WapcWinRt',
  'Invoke-WapcWinRtFindAll',
  'Get-WapcBluetoothSelectors',
  'Get-WapcBluetoothCandidates',
  'Merge-WapcBluetoothCandidates',
  'ConvertTo-WapcBluetoothCandidate',
  'Update-WapcBluetoothCandidatePairing',
  'Write-BluetoothCandidateLog'
)
