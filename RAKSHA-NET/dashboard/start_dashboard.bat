@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run setup_windows.bat first.
  pause
  exit /b 1
)
echo Close Arduino Serial Monitor before connecting to the board.
echo Keep this window open while you use the dashboard. Close it to stop.
".venv\Scripts\python.exe" run_dashboard.py %*
pause
