# 后台启动 Web 控制台（后端 + 前端）
# 用法：powershell -ExecutionPolicy Bypass -File scripts/windows/start_web.ps1
# 或双击：start_web.bat
#
# 启动后浏览器打开：http://localhost:5020
# 日志默认写到：%TEMP%\auto_tag_web_backend.log / auto_tag_web_frontend.log

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $ScriptDir "_uv_env.ps1")

$BackendPort = if ($env:PORT) { [int]$env:PORT } else { 8000 }
$FrontendPort = if ($env:FRONTEND_PORT) { [int]$env:FRONTEND_PORT } else { 5020 }
$BackendLog = if ($env:AUTO_TAG_BACKEND_LOG) {
    $env:AUTO_TAG_BACKEND_LOG
} else {
    Join-Path $env:TEMP "auto_tag_web_backend.log"
}
$FrontendLog = if ($env:AUTO_TAG_FRONTEND_LOG) {
    $env:AUTO_TAG_FRONTEND_LOG
} else {
    Join-Path $env:TEMP "auto_tag_web_frontend.log"
}

function Test-ListenPort([int]$Port) {
    $null -ne (
        Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
            Select-Object -First 1
    )
}

function Wait-HttpOk([string]$Url, [int]$TimeoutSec = 90) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        try {
            $resp = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3
            if ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 500) {
                return $true
            }
        } catch {
            Start-Sleep -Seconds 1
        }
    }
    return $false
}

function Add-NodeDirToPath([string]$dir) {
    if ($dir -and (Test-Path -LiteralPath $dir)) {
        if ($env:PATH -notlike "*$dir*") {
            $env:PATH = "$dir;$env:PATH"
        }
    }
}

Add-NodeDirToPath $env:NODE_DIR
Add-NodeDirToPath "D:\dev\node"
Add-NodeDirToPath (Join-Path $env:ProgramFiles "nodejs")
Add-NodeDirToPath (Join-Path ${env:ProgramFiles(x86)} "nodejs")

Write-Host "==> 启动 Auto Tag Web 控制台"
Write-Host "    仓库目录: $RepoRoot"
Write-Host "    后端日志: $BackendLog"
Write-Host "    前端日志: $FrontendLog"

$beUp = Test-ListenPort $BackendPort
$feUp = Test-ListenPort $FrontendPort
if ($beUp -and $feUp) {
    Write-Host "==> 服务似乎已在运行。"
    Write-Host "    请用浏览器打开: http://localhost:$FrontendPort"
    Write-Host "    若页面异常，请先双击 stop_web.bat 关闭，再重新 start_web.bat。"
    exit 0
}

if (-not (Get-Command node -ErrorAction SilentlyContinue) -and -not (Get-Command node.exe -ErrorAction SilentlyContinue)) {
    Write-Error "未找到 node（Node.js）。请先安装 Node.js，或设置环境变量 NODE_DIR 指向含 node.exe 的目录（例如 D:\dev\node）。"
}

function Start-DetachedScript([string]$ScriptPath, [string]$LogFile) {
    # 写临时 .cmd；优先 wmic 脱离当前会话（SSH 友好），失败则 Start-Process。
    # 日志用 cmd 重定向合并（PowerShell Start-Process 不能 stdout/stderr 同文件）。
    $launcher = Join-Path $env:TEMP ("auto_tag_launch_" + [guid]::NewGuid().ToString("N") + ".cmd")
    @(
        "@echo off",
        "cd /d `"$RepoRoot`"",
        "powershell -NoProfile -ExecutionPolicy Bypass -File `"$ScriptPath`" >> `"$LogFile`" 2>&1"
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
}

if (-not $beUp) {
    Write-Host "==> 启动后端 (端口 $BackendPort) ..."
    $backendScript = Join-Path $ScriptDir "run_web_backend.ps1"
    Start-DetachedScript $backendScript $BackendLog
} else {
    Write-Host "==> 后端端口 $BackendPort 已在监听，跳过启动"
}

if (-not $feUp) {
    Write-Host "==> 启动前端 (端口 $FrontendPort) ..."
    $frontendScript = Join-Path $ScriptDir "run_web_frontend_v2.ps1"
    Start-DetachedScript $frontendScript $FrontendLog
} else {
    Write-Host "==> 前端端口 $FrontendPort 已在监听，跳过启动"
}

Write-Host "==> 等待服务就绪（最多约 90 秒）..."
$okBe = Wait-HttpOk "http://127.0.0.1:$BackendPort/api/health" 90
$okFe = Wait-HttpOk "http://127.0.0.1:$FrontendPort/" 90

if ($okBe -and $okFe) {
    Write-Host ""
    Write-Host "==> 启动成功！"
    Write-Host "    请用浏览器打开控制台："
    Write-Host "    http://localhost:$FrontendPort"
    Write-Host ""
    Write-Host "    不用时请双击 stop_web.bat 关闭服务。"
    try {
        Start-Process "http://localhost:$FrontendPort"
    } catch { }
    exit 0
}

Write-Host ""
Write-Host "==> 启动未完全成功，请检查日志："
if (-not $okBe) { Write-Host "    后端未就绪 -> $BackendLog" }
if (-not $okFe) { Write-Host "    前端未就绪 -> $FrontendLog" }
Write-Host "    也可先 stop_web.bat，再重新 start_web.bat。"
exit 1
