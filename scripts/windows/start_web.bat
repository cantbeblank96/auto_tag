@echo off
cd /d "%~dp0"
echo.
echo ========== Auto Tag: starting Web console ==========
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_web.ps1"
set ERR=%ERRORLEVEL%
echo.
if %ERR% NEQ 0 (
  echo Start failed. Error code: %ERR%. Please screenshot this window.
) else (
  echo OK. You can close this window; services keep running in background.
)
echo.
pause
