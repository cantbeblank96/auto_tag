# 启动 Vite 前端（v2）
# 用法：powershell -ExecutionPolicy Bypass -File scripts/windows/run_web_frontend_v2.ps1
# 可选：$env:NODE_DIR 指向 Node 安装目录（含 npm.cmd），例如 D:\dev\node

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$WebDir = (Resolve-Path (Join-Path $ScriptDir "..\..\auto_tag\web")).Path

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

if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    Write-Error "未找到 npm。请安装 Node.js，或设置 `$env:NODE_DIR 后重试。"
}

Set-Location $WebDir
Write-Host "==> 启动 Vite 前端开发服务器 (http://localhost:5020)"
Write-Host "    API 请求会代理到 http://localhost:8000"
npm run dev -- --host 0.0.0.0 --port 5020
