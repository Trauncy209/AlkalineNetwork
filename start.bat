@echo off
title Alkaline Network Control Panel
echo.
echo ============================================
echo   Alkaline Network - Starting...
echo ============================================
echo.

REM Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found!
    echo Install from https://python.org
    pause
    exit /b 1
)

REM Install dependencies if needed
echo Checking dependencies...
pip show paramiko >nul 2>&1 || pip install paramiko
pip show requests >nul 2>&1 || pip install requests

echo.
echo Starting Control Panel...
echo.

python alkaline_control.py

pause
