@echo off
title Alkaline Network - Test Suite
color 0E

echo.
echo ============================================
echo   ALKALINE NETWORK - TEST SUITE
echo ============================================
echo.
echo   This tests the ENTIRE system without:
echo     - Touching real Stripe API
echo     - Charging real money
echo     - Connecting to real devices
echo.
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

echo Running full system tests...
echo.

python test_full_system.py

echo.
if errorlevel 1 (
    color 0C
    echo ============================================
    echo   TESTS FAILED - DO NOT DEPLOY
    echo ============================================
    echo.
    echo Some tests failed. Check the output above.
    echo DO NOT deploy to production until all tests pass.
) else (
    color 0A
    echo ============================================
    echo   ALL TESTS PASSED - SAFE TO DEPLOY
    echo ============================================
    echo.
    echo The system is secure:
    echo   - Unpaid customers cannot get internet
    echo   - Payment failures revoke access
    echo   - Fake keys are rejected
    echo   - Billing calculations are correct
)

echo.
pause
