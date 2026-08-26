#Requires -Version 5.1
# Post-pair PnP / audio endpoint verification.

function Test-BluetoothPairVerification {
  param(
    [string[]]$NamePatterns = @('EDIFIER', 'W800BT'),
    [int]$WaitSeconds = 30,
    [scriptblock]$Log = { param($m) Write-Host $m }
  )

  $deadline = (Get-Date).AddSeconds($WaitSeconds)
  $result = [ordered]@{
    bluetooth_pnp_ready    = $false
    a2dp_ready             = $false
    audio_endpoint_ready   = $false
    audio_endpoint_active  = $false
    bt_node_count          = 0
    media_count            = 0
    endpoint_count         = 0
  }

  & $Log 'Waiting for Bluetooth audio endpoint...'
  while ((Get-Date) -lt $deadline) {
    $edi = @(Get-PnpDevice -ErrorAction SilentlyContinue | Where-Object {
      $_.FriendlyName -match ($NamePatterns -join '|')
    })
    $media = @(Get-PnpDevice -ErrorAction SilentlyContinue | Where-Object {
      $_.FriendlyName -match ($NamePatterns -join '|') -and $_.Class -eq 'MEDIA'
    })
    $ep = @(Get-PnpDevice -Class AudioEndpoint -ErrorAction SilentlyContinue | Where-Object {
      $_.FriendlyName -match ($NamePatterns -join '|')
    })
    $epOk = @($ep | Where-Object { $_.Status -eq 'OK' })

    $result.bt_node_count = $edi.Count
    $result.media_count = $media.Count
    $result.endpoint_count = $ep.Count
    $result.bluetooth_pnp_ready = ($edi.Count -gt 0)
    $result.a2dp_ready = ($media.Count -gt 0)
    $result.audio_endpoint_ready = ($ep.Count -gt 0)
    $result.audio_endpoint_active = ($epOk.Count -gt 0)

    if ($result.audio_endpoint_ready) { break }
    Start-Sleep -Seconds 2
  }

  & $Log ('PnP Bluetooth node: ' + $(if ($result.bluetooth_pnp_ready) { 'FOUND' } else { 'MISSING' }))
  & $Log ('A2DP endpoint: ' + $(if ($result.a2dp_ready) { 'FOUND' } else { 'MISSING' }))
  & $Log ('Audio render endpoint: ' + $(if ($result.audio_endpoint_ready) { 'FOUND' } else { 'MISSING' }))

  return [pscustomobject]$result
}

Export-ModuleMember -Function @('Test-BluetoothPairVerification')
