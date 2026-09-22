@echo off
cd /d "%~dp0"
echo.
echo ========== Auto Tag: start Web (WSL backend) ==========
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_web_wsl.ps1"
set ERR=%ERRORLEVEL%
echo.
if %ERR% NEQ 0 (
  echo Start failed. Error code: %ERR%. See logs under repo logs\ or %%TEMP%%.
) else (
  echo OK. Open http://localhost:5020 in browser.
)
echo.
pause
