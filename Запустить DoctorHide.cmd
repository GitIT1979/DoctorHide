@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem Ищем Python: py launcher, python, python3 — без привязки к конкретному пути
set "PY="
where py >nul 2>&1 && set "PY=py -3"
if not defined PY where python >nul 2>&1 && set "PY=python"
if not defined PY where python3 >nul 2>&1 && set "PY=python3"
if not defined PY (
  echo Python не найден. Установите Python 3.9+ и добавьте его в PATH.
  pause
  exit /b 1
)

rem Если сервер уже поднят — второй экземпляр не запускаем
powershell -NoProfile -Command "try{(Invoke-WebRequest 'http://127.0.0.1:8765/api/status' -UseBasicParsing -TimeoutSec 2)|Out-Null;exit 0}catch{exit 1}" >nul 2>&1
if errorlevel 1 (
  start "DoctorHide server" cmd /k %PY% gui\server.py --port 8765
  timeout /t 3 /nobreak >nul
)

start "" http://127.0.0.1:8765/
exit /b 0
