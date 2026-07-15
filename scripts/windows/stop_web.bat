@echo off
cd /d "%~dp0"
echo.
echo ========== Auto Tag: stopping Web console ==========
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop_web.ps1"
echo.
echo Done. You can close this window.
echo.
pause
