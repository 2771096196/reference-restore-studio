@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Create .venv and install requirements.txt first. See README.md.
  pause
  exit /b 1
)
echo Open http://127.0.0.1:8191/ after the server starts.
".venv\Scripts\python.exe" server.py
pause
