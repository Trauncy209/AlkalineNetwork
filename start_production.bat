@echo off
title Alkaline Network - Production
color 0A

:menu
cls
echo.
echo ============================================
echo   ALKALINE NETWORK - MANAGEMENT
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

echo   1. FLASH DEVICES (Provision + Ship)
echo      ^> See pending orders, flash devices, ship to customers
echo.
echo   2. DASHBOARD (View customers + network)
echo      ^> Web UI to see customers, gateways, billing
echo.
echo   3. RUN MONTHLY BILLING
echo      ^> Charge customers, pay gateway hosts
echo.
echo   4. VIEW PENDING ORDERS
echo      ^> Quick list of orders waiting to be fulfilled
echo.
echo   5. BILLING SUMMARY
echo      ^> See revenue, costs, profit
echo.
echo   0. Exit
echo.
echo ============================================
echo.
set /p choice="Enter choice (0-5): "

if "%choice%"=="0" exit /b 0
if "%choice%"=="1" goto flash
if "%choice%"=="2" goto dashboard
if "%choice%"=="3" goto billing
if "%choice%"=="4" goto orders
if "%choice%"=="5" goto summary

echo Invalid choice
pause
goto menu

:flash
echo.
echo ============================================
echo   DEVICE PROVISIONING
echo ============================================
echo.
echo HOW THIS WORKS:
echo   1. Pending orders appear at top
echo   2. Plug in a blank Heltec HT-H7608 via Ethernet
echo   3. Click the order to select it
echo   4. Click GATEWAY or PINGER button
echo   5. Wait ~30 seconds
echo   6. Unplug, box, ship to address shown
echo.
echo Starting Flash Tool...
echo.
python flash_tool.py
pause
goto menu

:dashboard
echo.
set /p port="Dashboard port (default 8080): "
if "%port%"=="" set port=8080
echo.
echo Starting Dashboard on http://localhost:%port%
echo.
echo This shows:
echo   - All customers and their status
echo   - Gateway hosts and their customers
echo   - Billing status
echo   - Network health
echo.
start http://localhost:%port%
python alkaline_dashboard.py --port %port%
pause
goto menu

:billing
echo.
echo ============================================
echo   MONTHLY BILLING
echo ============================================
echo.
echo This will:
echo   - Charge all active customers via Stripe
echo   - Mark failed payments (they lose internet)
echo   - Calculate gateway host payouts ($2/customer)
echo   - Send payouts to gateway hosts
echo.
echo NOTE: Set up cron/Task Scheduler to run this
echo       automatically on the 1st of each month.
echo.
set /p confirm="Run billing now? (yes/no): "
if not "%confirm%"=="yes" (
    echo Cancelled.
    pause
    goto menu
)
echo.
python alkaline_billing.py --run-billing
pause
goto menu

:orders
echo.
echo ============================================
echo   PENDING ORDERS
echo ============================================
echo.
python provisioning.py --list-pending
pause
goto menu

:summary
echo.
echo ============================================
echo   BILLING SUMMARY
echo ============================================
echo.
python alkaline_billing.py --summary
pause
goto menu
