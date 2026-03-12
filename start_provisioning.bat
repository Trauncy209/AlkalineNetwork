@echo off
title Alkaline Network - Device Provisioning
color 0A

echo.
echo  ========================================
echo    ALKALINE NETWORK - DEVICE PROVISIONER
echo  ========================================
echo.
echo  THE ONE-CLICK FLOW:
echo    1. Customer pays on website
echo    2. Order appears in pending list
echo    3. Plug in blank device
echo    4. Click one button
echo    5. Device auto-configures
echo    6. Box and ship!
echo.
echo  ========================================
echo.

python alkaline_provisioning.py

pause
