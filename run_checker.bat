@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if errorlevel 1 (
  echo Python was not found.
  echo Install Python from https://www.python.org/downloads/windows/
  echo During setup, select "Add Python to PATH", then run this file again.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Preparing Windows Audio Path Checker for first use...
  py -m venv .venv
  if errorlevel 1 goto :failed
)

".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -e .
if errorlevel 1 goto :failed

".venv\Scripts\python.exe" -m audio_path_checker
if errorlevel 1 goto :failed
exit /b 0

:failed
echo.
echo The checker could not start. Copy the message above when reporting an issue.
pause
exit /b 1

