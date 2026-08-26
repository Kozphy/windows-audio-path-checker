#Requires -Version 5.1
# Rank candidates via Python with deterministic PowerShell fallback.

function Get-PythonRankerPath {
  $repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
  $venvPy = Join-Path $repoRoot '.venv\Scripts\python.exe'
  if (Test-Path -LiteralPath $venvPy) { return $venvPy }
  return 'python'
}

function ConvertTo-WapcCandidateExportObject {
  param($Item)
  $o = [ordered]@{}
  if ($Item -is [System.Collections.IDictionary]) {
    foreach ($k in $Item.Keys) {
      if ($k -in @('device_ref', 'properties')) { continue }
      $o[$k] = $Item[$k]
    }
  } else {
    foreach ($p in $Item.PSObject.Properties) {
      if ($p.Name -in @('device_ref', 'properties')) { continue }
      $o[$p.Name] = $p.Value
    }
  }
  return [pscustomobject]$o
}

function ConvertTo-WapcJsonArray {
  param([array]$Items, [int]$Depth = 8)
  if (-not $Items -or $Items.Count -eq 0) { return '[]' }
  if ($Items.Count -eq 1) {
    return '[' + ($Items[0] | ConvertTo-Json -Depth $Depth -Compress) + ']'
  }
  return ($Items | ConvertTo-Json -Depth $Depth -Compress)
}

function Invoke-PythonRankerProcess {
  param(
    [string]$PythonExe,
    [string]$JsonPayload,
    [string]$TargetName,
    [string]$TargetAddress,
    [string]$RepoRoot,
    [bool]$ClassicEnumOk = $true,
    [bool]$AepEnumOk = $true
  )
  $flagClassic = if ($ClassicEnumOk) { '--classic-enum-ok' } else { '' }
  $flagAep = if ($AepEnumOk) { '--aep-enum-ok' } else { '' }
  $psi = New-Object System.Diagnostics.ProcessStartInfo
  $psi.FileName = $PythonExe
  $psi.Arguments = (
    '-m audio_path_checker.bluetooth_pairing rank ' +
    '--target-name "' + $TargetName + '" ' +
    '--target-address ' + $TargetAddress + ' ' +
    $flagClassic + ' ' + $flagAep + ' --json'
  ).Trim()
  $psi.UseShellExecute = $false
  $psi.RedirectStandardInput = $true
  $psi.RedirectStandardOutput = $true
  $psi.RedirectStandardError = $true
  $psi.CreateNoWindow = $true
  $psi.WorkingDirectory = $RepoRoot
  $prevPath = $env:PYTHONPATH
  $env:PYTHONPATH = if ($prevPath) { "$RepoRoot;$prevPath" } else { $RepoRoot }
  try {
    $proc = [System.Diagnostics.Process]::Start($psi)
    $proc.StandardInput.Write($JsonPayload)
    $proc.StandardInput.Close()
    $stdout = $proc.StandardOutput.ReadToEnd()
    $stderr = $proc.StandardError.ReadToEnd()
    $proc.WaitForExit()
    return @{ ExitCode = $proc.ExitCode; StdOut = $stdout; StdErr = $stderr }
  } finally {
    if ($null -eq $prevPath) { Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue }
    else { $env:PYTHONPATH = $prevPath }
  }
}

function Rank-WapcBluetoothCandidatesFallback {
  param(
    [array]$Candidates,
    [string]$TargetName,
    [string]$TargetAddress,
    [bool]$ClassicEnumOk,
    [bool]$AepEnumOk
  )
  $ranked = New-Object System.Collections.ArrayList
  foreach ($c in $Candidates) {
    $score = 0
    $components = New-Object System.Collections.ArrayList
    if (-not $c.enumeration_succeeded) {
      $score -= 200
      [void]$components.Add('-200 enumeration failed')
    } else {
      if ([string]$c.name -eq $TargetName) { $score += 100; [void]$components.Add('+100 exact name') }
      if ($c.can_pair) { $score += 40; [void]$components.Add('+40 CanPair') } else { $score -= 100 }
      if ($c.is_classic) { $score += 25; [void]$components.Add('+25 classic') }
      if ($c.is_ble) { $score -= 80; [void]$components.Add('-80 ble') }
      if ($c.kind -eq 'AssociationEndpoint') { $score += 15 }
      if (-not $c.is_paired) { $score += 10 }
    }
    [void]$ranked.Add([pscustomobject]@{
      name = $c.name; id = $c.id; kind = $c.kind; can_pair = $c.can_pair
      is_paired = $c.is_paired; protocol_id = $c.protocol_id; device_address = $c.device_address
      score = $score; score_components = @($components); classification = 'Fallback'
    })
  }
  $sorted = @($ranked | Sort-Object score -Descending)
  for ($i = 0; $i -lt $sorted.Count; $i++) { $sorted[$i] | Add-Member -NotePropertyName rank -NotePropertyValue ($i + 1) -Force }

  $pairability = 'UNKNOWN'
  if (-not $ClassicEnumOk -and -not $AepEnumOk) { $pairability = 'UNKNOWN' }
  else {
    $classic = @($Candidates | Where-Object { $_.is_classic -and $_.enumeration_succeeded })
    if ($classic.Count -eq 0 -and -not $ClassicEnumOk) { $pairability = 'UNKNOWN' }
    elseif (@($classic | Where-Object { $_.can_pair -and -not $_.is_paired }).Count -gt 0) { $pairability = 'PAIRABLE' }
    elseif ($classic.Count -gt 0) { $pairability = 'NOT_PAIRABLE' }
  }

  $selected = $null
  if ($pairability -ne 'UNKNOWN') {
    foreach ($c in $sorted) {
      if ($c.can_pair -and -not $c.is_paired) { $selected = $c; break }
      if ($c.is_paired) { $selected = $c; break }
    }
  }

  return [pscustomobject]@{
    ranked = $sorted
    selected = $selected
    pairability = $pairability
    pairable_found = ($null -ne $selected -and $selected.can_pair)
    ranker_source = 'powershell_fallback'
  }
}

function Rank-WapcBluetoothCandidates {
  param(
    [Parameter(Mandatory)][array]$Candidates,
    [string]$TargetName = 'EDIFIER W800BT Pro',
    [string]$TargetAddress = 'c8247887e57c',
    [bool]$ClassicEnumOk = $true,
    [bool]$AepEnumOk = $true,
    [scriptblock]$Log = { param($m) Write-Host $m }
  )

  if (-not $Candidates -or $Candidates.Count -eq 0) {
    return [pscustomobject]@{ ranked = @(); selected = $null; pairability = 'UNKNOWN'; pairable_found = $false; ranker_source = 'none' }
  }

  $export = @($Candidates | ForEach-Object { ConvertTo-WapcCandidateExportObject $_ })
  $json = ConvertTo-WapcJsonArray -Items $export -Depth 10
  $py = Get-PythonRankerPath
  $repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
  $result = Invoke-PythonRankerProcess -PythonExe $py -JsonPayload $json `
    -TargetName $TargetName -TargetAddress $TargetAddress -RepoRoot $repoRoot `
    -ClassicEnumOk:$ClassicEnumOk -AepEnumOk:$AepEnumOk

  if ($result.ExitCode -eq 0) {
    try {
      $parsed = ($result.StdOut | Out-String).Trim() | ConvertFrom-Json
      $parsed | Add-Member -NotePropertyName ranker_source -NotePropertyValue 'python' -Force
      return $parsed
    } catch {
      & $Log ('Python rank JSON parse failed, using fallback: ' + $_.Exception.Message)
    }
  } else {
    $msg = ($result.StdOut + ' ' + $result.StdErr).Trim()
    & $Log ('Python ranker unavailable, using fallback: ' + $(if ($msg) { $msg } else { 'exit ' + $result.ExitCode }))
  }

  return Rank-WapcBluetoothCandidatesFallback -Candidates $Candidates -TargetName $TargetName `
    -TargetAddress $TargetAddress -ClassicEnumOk:$ClassicEnumOk -AepEnumOk:$AepEnumOk
}

function Write-BluetoothCandidateRanking {
  param($RankResult)
  if (-not $RankResult -or -not $RankResult.ranked) { return }
  Write-Host ''
  Write-Host 'Candidate ranking:'
  Write-Host ('Pairability: {0} (source: {1})' -f $RankResult.pairability, $RankResult.ranker_source)
  Write-Host ''
  foreach ($c in $RankResult.ranked) {
    Write-Host ('#{0} score={1}' -f $c.rank, $c.score)
    Write-Host ('  {0}' -f $c.name)
    Write-Host ('  CanPair={0} Kind={1} Class={2}' -f $c.can_pair, $c.kind, $c.classification)
    if ($c.score_components) {
      foreach ($line in $c.score_components) { Write-Host ('    {0}' -f $line) }
    }
    Write-Host ''
  }
}

Export-ModuleMember -Function @(
  'Rank-WapcBluetoothCandidates',
  'Write-BluetoothCandidateRanking',
  'ConvertTo-WapcJsonArray',
  'Get-PythonRankerPath'
)

# Back-compat alias
Set-Alias -Name Invoke-BluetoothCandidateRanker -Value Rank-WapcBluetoothCandidates -Scope Local
