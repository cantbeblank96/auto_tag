# Restart Web console (WSL backend + Windows frontend, kevin_sdk)
# Usage: powershell -ExecutionPolicy Bypass -File scripts/windows/restart_web_wsl.ps1
# Or double-click: restart_web_wsl.bat

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $ScriptDir "_wsl_util.ps1")

$BackendPort = if ($env:PORT) { [int]$env:PORT } else { 8000 }

Write-Host "==> Restart Auto Tag Web (WSL backend): stopping ..."
& (Join-Path $ScriptDir "stop_web.ps1")
Stop-WslPort $BackendPort
Start-Sleep -Seconds 1

Write-Host ""
Write-Host "==> Starting WSL backend + Windows frontend ..."
& (Join-Path $ScriptDir "start_web_wsl.ps1")
exit $LASTEXITCODE
