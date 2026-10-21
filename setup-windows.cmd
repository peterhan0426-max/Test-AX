@echo off
cd /d "%~dp0"
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)" >nul 2>&1
if errorlevel 1 (
  echo Python 3.11 or newer with the Python launcher is required.
  echo Install Python, then run START.cmd again.
  start "" https://www.python.org/downloads/windows/
  exit /b 1
)
py -3 -m venv .venv
if errorlevel 1 exit /b 1
.venv\Scripts\python -m pip install -r requirements.txt
if errorlevel 1 exit /b 1
.venv\Scripts\python -m playwright install chromium
if errorlevel 1 exit /b 1
type nul > .venv\collector-ready
echo Setup complete.
