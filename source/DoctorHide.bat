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

REM Launch GUI server. Redirect stdout/stderr to log for diagnostics (window is minimized).
start "DoctorHide" /min cmd /c "%PY% gui\server.py --port 8765 > doctorhide.log 2>&1"

REM Poll HTTP endpoint until ready or ~15 sec timeout.
REM server.py opens the browser itself, so we only check readiness.
set /a tries=0
:waitloop
set /a tries+=1
if %tries% gtr 30 (
    echo Server did not respond within 15 sec.
    echo See doctorhide.log for details.
    pause
    exit /b 1
)
powershell -NoProfile -Command "try{(Invoke-WebRequest -UseBasicParsing -Uri http://127.0.0.1:8765/ -TimeoutSec 1).StatusCode}catch{exit 1}" >nul 2>&1
if errorlevel 1 (
    timeout /t 1 /nobreak >nul
    goto waitloop
)
exit /b 0
