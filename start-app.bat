@echo off
rem Double-click to run invoicing_io for everyday use (this PC + your phone via Tailscale).
rem Keep the window open while you use the app; close it to stop.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo The Python environment .venv is missing. See README section 2 "First-time setup".
  pause
  exit /b 1
)
".venv\Scripts\python.exe" serve.py
pause
