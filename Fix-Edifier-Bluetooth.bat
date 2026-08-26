@echo off
setlocal
set "SCRIPT=%~dp0scripts\wapc-bt-auto-pair.ps1"
echo WAPC Bluetooth Recovery - ranked auto-pair (requires Administrator).
echo BEFORE AUTO-PAIR: hold earphone power until LED flashes pairing mode.
if not exist "%SCRIPT%" (
  echo Script not found: %SCRIPT%
  pause
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" -TargetName "EDIFIER W800BT Pro" -TargetAddress "c8247887e57c" %*
pause
