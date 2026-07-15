# 由 API /utils/restart_backend 调用：释放端口后拉起后端。

$ErrorActionPreference = "SilentlyContinue"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = (Resolve-Path (Join-Path $ScriptDir "..\..")).Path
$Port = if ($env:PORT) { [int]$env:PORT } else { 8000 }
$LogsDir = Join-Path $RepoRoot "logs"
if (-not (Test-Path -LiteralPath $LogsDir)) {
    New-Item -ItemType Directory -Path $LogsDir -Force | Out-Null
}
$LogFile = if ($env:AUTO_TAG_BACKEND_LOG) {
    $env:AUTO_TAG_BACKEND_LOG
} else {
    Join-Path $LogsDir "auto_tag_backend.log"
}

Start-Sleep -Seconds 1

Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue |
    ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }

Start-Sleep -Milliseconds 600

Set-Location $RepoRoot
$runScript = Join-Path $ScriptDir "run_web_backend.ps1"

# 不能把 RedirectStandardOutput/Error 指到同一文件；用 cmd 合并重定向。
# 优先 wmic 脱离会话，避免随父进程退出而被杀掉。
$launcher = Join-Path $env:TEMP ("auto_tag_restart_backend_" + [guid]::NewGuid().ToString("N") + ".cmd")
@(
    "@echo off",
    "cd /d `"$RepoRoot`"",
    "powershell -NoProfile -ExecutionPolicy Bypass -File `"$runScript`" >> `"$LogFile`" 2>&1"
) | Set-Content -LiteralPath $launcher -Encoding ASCII

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
