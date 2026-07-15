# 一键重启 Web 控制台（先关后开）
# 用法：powershell -ExecutionPolicy Bypass -File scripts/windows/restart_web.ps1
# 或双击：restart_web.bat

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host "==> 重启 Auto Tag Web：先关闭..."
& (Join-Path $ScriptDir "stop_web.ps1")
Start-Sleep -Seconds 1
Write-Host ""
Write-Host "==> 再启动..."
& (Join-Path $ScriptDir "start_web.ps1")
exit $LASTEXITCODE
