# Start Web console: WSL backend (kevin_sdk) + Windows frontend
# Usage: powershell -ExecutionPolicy Bypass -File scripts/windows/start_web_wsl.ps1
# Or double-click: start_web_wsl.bat

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $ScriptDir "_wsl_util.ps1")

$RepoRoot = (Resolve-Path (Join-Path $ScriptDir "..\..")).Path
$BackendPort = if ($env:PORT) { [int]$env:PORT } else { 8000 }
$FrontendPort = if ($env:FRONTEND_PORT) { [int]$env:FRONTEND_PORT } else { 5020 }
$BackendLog = Join-Path $RepoRoot "logs\wsl_backend.log"
$FrontendLog = if ($env:AUTO_TAG_FRONTEND_LOG) {
    $env:AUTO_TAG_FRONTEND_LOG
} else {
    Join-Path $env:TEMP "auto_tag_web_frontend.log"
}

function Add-NodeDirToPath([string]$dir) {
    if ($dir -and (Test-Path -LiteralPath $dir)) {
        if ($env:PATH -notlike "*$dir*") {
            $env:PATH = "$dir;$env:PATH"
        }
    }
}

function Start-DetachedScript([string]$ScriptPath, [string]$LogFile, [hashtable]$ExtraEnv) {
    $launcher = Join-Path $env:TEMP ("auto_tag_launch_" + [guid]::NewGuid().ToString("N") + ".cmd")
    $envLines = @()
    if ($ExtraEnv) {
        foreach ($key in $ExtraEnv.Keys) {
            $envLines += "set $key=$($ExtraEnv[$key])"
        }
    }
    @(
        "@echo off",
        $envLines,
        "cd /d `"$RepoRoot`"",
        "powershell -NoProfile -ExecutionPolicy Bypass -File `"$ScriptPath`" >> `"$LogFile`" 2>&1"
    ) | ForEach-Object { $_ } | Set-Content -LiteralPath $launcher -Encoding ASCII

    $started = $false
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $createCmd = "cmd.exe /c `"$launcher`""
        $wmicOut = cmd.exe /c "wmic process call create `"$createCmd`"" 2>&1 | Out-String
        if ($wmicOut -match "ReturnValue\s*=\s*0") {
            $started = $true
        }
    } catch {
        $started = $false
    } finally {
        $ErrorActionPreference = $prevEap
    }

    if (-not $started) {
        Start-Process -FilePath "cmd.exe" -ArgumentList @("/c", "`"$launcher`"") -WindowStyle Hidden | Out-Null
    }
}

Add-NodeDirToPath $env:NODE_DIR
Add-NodeDirToPath "D:\dev\node"
Add-NodeDirToPath (Join-Path $env:ProgramFiles "nodejs")
Add-NodeDirToPath (Join-Path ${env:ProgramFiles(x86)} "nodejs")

Write-Host "==> Starting Auto Tag Web (WSL backend + Windows frontend)"
Write-Host "    Repo: $RepoRoot"
Write-Host "    WSL backend log: $BackendLog"
Write-Host "    Frontend log: $FrontendLog"

$beUp = Test-ListenPort $BackendPort
$feUp = Test-ListenPort $FrontendPort
if ($beUp -and $feUp) {
    Write-Host "==> Services already listening."
    Write-Host "    Open: http://localhost:$FrontendPort"
    Write-Host "    If annotation tools unavailable, run restart_web_wsl.bat"
    exit 0
}

$hasNode = (Get-Command node.exe -ErrorAction SilentlyContinue) -or (Get-Command node -ErrorAction SilentlyContinue)
if (-not $hasNode) {
    throw "Node.js not found. Install Node.js or set NODE_DIR."
}

if (-not $beUp) {
    Write-Host "==> Starting WSL backend on port $BackendPort ..."
    & (Join-Path $ScriptDir "run_web_backend_wsl.ps1")
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} else {
    Write-Host "==> Backend port $BackendPort already listening, skip WSL backend"
}

if (-not $feUp) {
    Write-Host "==> Starting frontend on port $FrontendPort ..."
    $frontendScript = Join-Path $ScriptDir "run_web_frontend_v2.ps1"
    $apiProxy = Get-WslBackendBaseUrl $BackendPort
    Write-Host "    Vite API proxy -> $apiProxy"
    Start-DetachedScript $frontendScript $FrontendLog @{ AUTO_TAG_API_PROXY = $apiProxy }
} else {
    Write-Host "==> Frontend port $FrontendPort already listening, skip"
}

$backendUrl = Get-WslBackendBaseUrl $BackendPort
Write-Host "==> Waiting for services (up to 90s) ..."
$okBe = Wait-HttpOk "$backendUrl/api/health" 90
$okFe = Wait-HttpOk "http://127.0.0.1:$FrontendPort/" 90

if ($okBe -and $okFe) {
    Write-Host ""
    Write-Host "==> Started successfully."
    Write-Host "    Console: http://localhost:$FrontendPort"
    Write-Host "    Stop with stop_web.bat"
    try { Start-Process "http://localhost:$FrontendPort" } catch { }
    exit 0
}

Write-Host ""
Write-Host "==> Startup incomplete. Check logs:"
if (-not $okBe) { Write-Host "    WSL backend -> $BackendLog" }
if (-not $okFe) { Write-Host "    Frontend -> $FrontendLog" }
exit 1
