@echo off
cd /d "%~dp0"

set "PY="
where py >nul 2>&1 && set "PY=py -3"
if not defined PY where python >nul 2>&1 && set "PY=python"
if not defined PY where python3 >nul 2>&1 && set "PY=python3"
if not defined PY (
    echo Python not found. Install Python 3.9+ and add to PATH.
    pause
    exit /b 1
)

start "DoctorHide" /min %PY% gui\server.py --port 8765
timeout /t 2 /nobreak >nul
start "" http://127.0.0.1:8765/
