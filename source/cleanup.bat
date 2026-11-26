@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ========================================
echo   DoctorHide cleanup
echo ========================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo [Error] Python not found in PATH.
    pause
    exit /b 1
)

echo   1. Show what would be deleted
echo   2. Delete work artifacts
echo   3. Delete all extra including gui
echo   0. Exit
echo.
set /p choice=Choice [1]: 

if "%choice%"=="" set choice=1
if "%choice%"=="0" exit /b 0
if "%choice%"=="1" (
    python cleanup.py
    goto end
)
if "%choice%"=="2" (
    echo.
    set /p confirm=Type YES to delete: 
    if /i not "%confirm%"=="YES" (
        echo Cancelled.
        goto end
    )
    python cleanup.py --yes
    goto end
)
if "%choice%"=="3" (
    echo.
    set /p confirm=Type YES to delete all: 
    if /i not "%confirm%"=="YES" (
        echo Cancelled.
        goto end
    )
    python cleanup.py --yes --all
    goto end
)

echo Unknown choice.
:end
echo.
pause
