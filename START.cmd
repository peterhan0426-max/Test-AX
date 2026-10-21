@echo off
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  call setup-windows.cmd
  if errorlevel 1 goto failed
)
if not exist .venv\collector-ready (
  .venv\Scripts\python -m pip install -r requirements.txt
  if errorlevel 1 goto failed
  .venv\Scripts\python -m playwright install chromium
  if errorlevel 1 goto failed
  type nul > .venv\collector-ready
)
.venv\Scripts\python -m collector.capture
if errorlevel 1 goto failed
pause
exit /b 0
:failed
echo.
echo Setup or startup failed. Keep this window open and share the error text.
echo Do not share passwords or customer information.
pause
exit /b 1
