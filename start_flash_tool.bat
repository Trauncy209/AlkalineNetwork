@echo off
title Alkaline Network Flash Tool
echo ============================================
echo   Alkaline Network - Heltec Flash Tool
echo ============================================
echo.

REM Check if Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python is not installed or not in PATH!
    echo.
    echo Please install Python from https://python.org
    echo Make sure to check "Add Python to PATH" during installation.
    echo.
    pause
    exit /b 1
)

echo Checking dependencies...
python -c "import paramiko" >nul 2>&1
if errorlevel 1 (
    echo Installing paramiko...
    pip install paramiko
    echo.
)

echo Starting Flash Tool...
echo.
python flash_tool.py

if errorlevel 1 (
    echo.
    echo Flash tool encountered an error.
    pause
)
