@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run setup_windows.bat first.
  pause
  exit /b 1
)
echo Starting the dashboard in DEMO mode: recorded data, not live hardware.
".venv\Scripts\python.exe" run_dashboard.py --demo %*
pause
