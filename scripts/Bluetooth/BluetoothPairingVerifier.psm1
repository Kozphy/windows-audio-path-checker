#Requires -Version 5.1
# Post-pair PnP / A2DP / audio endpoint verification — identity-correlated only.
#
# Never treat "any Bluetooth headset endpoint" as proof the configured target recovered.

Import-Module (Join-Path $PSScriptRoot 'WapcBluetoothIdentity.psm1') -Force -Global

function Test-BluetoothPairVerification {
  param(
    [string[]]$NamePatterns = @('W800BT'),
    [string]$DeviceAddress = '',
    [string]$TargetName = '',
    [int]$WaitSeconds = 30,
    [scriptblock]$Log = { param($m) Write-Host $m }
  )

  $identity = New-WapcTargetIdentity -RequestedName $TargetName -BluetoothAddress $DeviceAddress
  $deadline = (Get-Date).AddSeconds($WaitSeconds)
  $result = [ordered]@{
    schema_version                    = 2
    bluetooth_pnp_ready               = $false
    a2dp_ready                        = $false
    audio_endpoint_ready              = $false
    audio_endpoint_active             = $false
    exact_target_pnp_node_found       = $false
    exact_target_a2dp_endpoint_found  = $false
    exact_target_audio_endpoint_found = $false
    a2dp_endpoint_identity_match      = $false
    audio_endpoint_identity_match     = $false
    bt_node_count                     = 0
    media_count                       = 0
    endpoint_count                    = 0
    matched_names                     = @()
    matched_endpoint_ids              = @()
    unrelated_endpoints               = New-Object System.Collections.ArrayList
    evidence_graph                    = New-Object System.Collections.ArrayList
    expected_target                   = [ordered]@{
      name    = $TargetName
      address = (ConvertTo-WapcNormalizedBluetoothAddress $DeviceAddress)
    }
  }

  function Test-NodeIdentity {
    param($Device)
    $obs = @{
      name            = $Device.FriendlyName
      InstanceId      = $Device.InstanceId
      device_address  = (Get-WapcAddressFromInstanceId $Device.InstanceId)
    }
    return (Test-WapcBluetoothIdentityMatch -ExpectedTarget $identity -ObservedDevice $obs)
  }

  & $Log 'Waiting for exact-target Bluetooth audio endpoint...'
  while ((Get-Date) -lt $deadline) {
    $allBt = @(Get-PnpDevice -Class Bluetooth -ErrorAction SilentlyContinue)
    $allMedia = @(Get-PnpDevice -ErrorAction SilentlyContinue | Where-Object { $_.Class -eq 'MEDIA' })
    $allEp = @(Get-PnpDevice -Class AudioEndpoint -ErrorAction SilentlyContinue)

    $edi = New-Object System.Collections.ArrayList
    $media = New-Object System.Collections.ArrayList
    $ep = New-Object System.Collections.ArrayList
    $result.unrelated_endpoints = New-Object System.Collections.ArrayList
    $result.evidence_graph = New-Object System.Collections.ArrayList

    foreach ($d in $allBt) {
      $m = Test-NodeIdentity $d
      if ($m.matched) {
        [void]$edi.Add($d)
        [void]$result.evidence_graph.Add([ordered]@{
          source = 'TargetIdentity'; target = $d.InstanceId
          relationship = $m.match_method; confidence = $m.confidence
        })
      } elseif ($d.FriendlyName) {
        [void]$result.unrelated_endpoints.Add([ordered]@{
          class = 'Bluetooth'; name = $d.FriendlyName
          address = $m.observed_address; reason = $m.reason
        })
      }
    }
    foreach ($d in $allMedia) {
      $m = Test-NodeIdentity $d
      if ($m.matched) {
        [void]$media.Add($d)
        [void]$result.evidence_graph.Add([ordered]@{
          source = 'TargetIdentity'; target = $d.InstanceId
          relationship = $m.match_method; confidence = $m.confidence
        })
      } elseif ($d.FriendlyName) {
        [void]$result.unrelated_endpoints.Add([ordered]@{
          class = 'MEDIA'; name = $d.FriendlyName
          address = $m.observed_address; reason = $m.reason
        })
      }
    }
    foreach ($d in $allEp) {
      $m = Test-NodeIdentity $d
      if ($m.matched) {
        [void]$ep.Add($d)
        [void]$result.evidence_graph.Add([ordered]@{
          source = 'TargetIdentity'; target = $d.InstanceId
          relationship = $m.match_method; confidence = $m.confidence
        })
      } elseif ($d.FriendlyName) {
        [void]$result.unrelated_endpoints.Add([ordered]@{
          class = 'AudioEndpoint'; name = $d.FriendlyName
          address = $m.observed_address; reason = $m.reason
        })
      }
    }

    $epOk = @($ep | Where-Object { $_.Status -eq 'OK' })
    $result.bt_node_count = $edi.Count
    $result.media_count = $media.Count
    $result.endpoint_count = $ep.Count
    $result.exact_target_pnp_node_found = ($edi.Count -gt 0)
    $result.exact_target_a2dp_endpoint_found = ($media.Count -gt 0)
    $result.exact_target_audio_endpoint_found = ($ep.Count -gt 0)
    $result.a2dp_endpoint_identity_match = ($media.Count -gt 0)
    $result.audio_endpoint_identity_match = ($ep.Count -gt 0)
    # Compat aliases — same semantics as exact-target flags (schema_version 2).
    $result.bluetooth_pnp_ready = $result.exact_target_pnp_node_found
    $result.a2dp_ready = $result.exact_target_a2dp_endpoint_found
    $result.audio_endpoint_ready = $result.exact_target_audio_endpoint_found
    $result.audio_endpoint_active = ($epOk.Count -gt 0)
    $result.matched_names = @($edi + $media + $ep | ForEach-Object { $_.FriendlyName } | Select-Object -Unique)
    $result.matched_endpoint_ids = @($ep | ForEach-Object { $_.InstanceId })

    if ($result.exact_target_audio_endpoint_found -and $result.exact_target_a2dp_endpoint_found) { break }
    Start-Sleep -Seconds 2
  }

  & $Log ('Exact-target PnP Bluetooth node: ' + $(if ($result.exact_target_pnp_node_found) { 'FOUND' } else { 'NOT_FOUND' }))
  & $Log ('Exact-target A2DP endpoint: ' + $(if ($result.exact_target_a2dp_endpoint_found) { 'FOUND' } else { 'NOT_FOUND' }))
  & $Log ('Exact-target audio render endpoint: ' + $(if ($result.exact_target_audio_endpoint_found) { 'FOUND' } else { 'NOT_FOUND' }))
  if ($result.matched_names.Count -gt 0) {
    & $Log ('Matched: ' + ($result.matched_names -join ', '))
  }
  if ($result.unrelated_endpoints.Count -gt 0) {
    & $Log ('Unrelated Bluetooth/audio nodes ignored: ' + $result.unrelated_endpoints.Count)
  }

  return [pscustomobject]$result
}

Export-ModuleMember -Function @('Test-BluetoothPairVerification')
