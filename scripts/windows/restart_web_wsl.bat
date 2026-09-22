@echo off
cd /d "%~dp0"
echo.
echo ========== Auto Tag: restart Web (WSL backend) ==========
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0restart_web_wsl.ps1"
set ERR=%ERRORLEVEL%
echo.
if %ERR% NEQ 0 (
  echo Restart failed. Error code: %ERR%. Please screenshot this window.
) else (
  echo OK. Services keep running in background.
)
echo.
pause
