# 由 API /utils/restart_backend 调用：释放端口后拉起后端。

$ErrorActionPreference = "SilentlyContinue"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = (Resolve-Path (Join-Path $ScriptDir "..\..")).Path
$Port = if ($env:PORT) { [int]$env:PORT } else { 8000 }
$LogFile = if ($env:AUTO_TAG_BACKEND_LOG) { $env:AUTO_TAG_BACKEND_LOG } else { Join-Path $env:TEMP "auto_tag_backend.log" }

Start-Sleep -Seconds 1

Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue |
    ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }

Start-Sleep -Milliseconds 600

Set-Location $RepoRoot
$runScript = Join-Path $ScriptDir "run_web_backend.ps1"
Start-Process -FilePath "powershell" -ArgumentList @(
    "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $runScript
) -RedirectStandardOutput $LogFile -RedirectStandardError $LogFile -WindowStyle Hidden
