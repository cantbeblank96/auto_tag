# Start Auto Tag backend inside WSL (kevin_sdk annotation tools)
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File scripts/windows/run_web_backend_wsl.ps1

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $ScriptDir "_wsl_util.ps1")

$RepoRoot = (Resolve-Path (Join-Path $ScriptDir "..\..")).Path
$BackendPort = if ($env:PORT) { [int]$env:PORT } else { 8000 }
$WslStartScript = Get-WslBackendStartScript $RepoRoot
$BackendLog = Join-Path $RepoRoot "logs\wsl_backend.log"

Write-Host "==> Free backend port $BackendPort (Windows listeners + WSL) ..."
if (Test-ListenPort $BackendPort) {
    $pids = @(
        Get-NetTCPConnection -LocalPort $BackendPort -State Listen -ErrorAction SilentlyContinue |
            ForEach-Object { $_.OwningProcess }
    ) | Where-Object { $_ -and $_ -gt 0 } | Select-Object -Unique
    foreach ($procId in $pids) {
        Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
        Write-Host "  Stopped Windows PID=$procId on port $BackendPort"
    }
}
Stop-WslPort $BackendPort

Write-Host "==> Start WSL backend: $WslStartScript"
wsl -e bash $WslStartScript
if ($LASTEXITCODE -ne 0) {
    Write-Error "WSL backend failed. See $BackendLog"
}

$backendUrl = Get-WslBackendBaseUrl $BackendPort
if (-not (Wait-HttpOk "$backendUrl/api/health" 120)) {
    Write-Error "Backend health check failed from Windows. See $BackendLog"
}

Write-Host "==> WSL backend: $backendUrl"
Write-Host "    (Vite proxy uses AUTO_TAG_API_PROXY when started via start_web_wsl.ps1)"
