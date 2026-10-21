@echo off
setlocal
cd /d "%~dp0"
py -3 -m saenong.desktop
if errorlevel 1 (
  echo Python 3.11 or newer is required. See README.md.
  pause
  exit /b 1
)
