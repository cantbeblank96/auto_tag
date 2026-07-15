# 启动 Vite 前端（v2）
# 用法：powershell -ExecutionPolicy Bypass -File scripts/windows/run_web_frontend_v2.ps1
# 可选：$env:NODE_DIR 指向 Node 安装目录（含 npm.cmd / node.exe），例如 D:\dev\node

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$WebDir = (Resolve-Path (Join-Path $ScriptDir "..\..\auto_tag\web")).Path
$FrontendPort = if ($env:FRONTEND_PORT) { $env:FRONTEND_PORT } else { "5020" }

function Add-NodeDirToPath([string]$dir) {
    if ($dir -and (Test-Path -LiteralPath $dir)) {
        if ($env:PATH -notlike "*$dir*") {
            $env:PATH = "$dir;$env:PATH"
        }
    }
}

# 常见便携/自定义安装路径
Add-NodeDirToPath $env:NODE_DIR
Add-NodeDirToPath "D:\dev\node"
Add-NodeDirToPath (Join-Path $env:ProgramFiles "nodejs")
Add-NodeDirToPath (Join-Path ${env:ProgramFiles(x86)} "nodejs")

$nodeCmd = Get-Command node.exe -ErrorAction SilentlyContinue
if (-not $nodeCmd) { $nodeCmd = Get-Command node -ErrorAction SilentlyContinue }
if (-not $nodeCmd) {
    Write-Error "未找到 node。请安装 Node.js，或设置 `$env:NODE_DIR 后重试。"
}

$viteJs = Join-Path $WebDir "node_modules\vite\bin\vite.js"
if (-not (Test-Path -LiteralPath $viteJs)) {
    Write-Error "未找到 Vite：$viteJs`n请先在 auto_tag\web 下执行 npm install。"
}

Set-Location $WebDir
Write-Host "==> 启动 Vite 前端开发服务器 (http://localhost:$FrontendPort)"
Write-Host "    API 请求会代理到 http://localhost:8000"
# 直接调用 vite.js，避开 npm.cmd 在 PowerShell 下吞掉 --host/--port 的问题
& $nodeCmd.Source $viteJs --host 0.0.0.0 --port $FrontendPort
