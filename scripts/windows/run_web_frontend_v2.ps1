# 启动 Vite 前端（v2）
# 用法：powershell -ExecutionPolicy Bypass -File scripts/windows/run_web_frontend_v2.ps1

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$WebDir = (Resolve-Path (Join-Path $ScriptDir "..\..\auto_tag\web")).Path

Set-Location $WebDir
Write-Host "==> 启动 Vite 前端开发服务器 (http://localhost:5020)"
Write-Host "    API 请求会代理到 http://localhost:8000"
npm run dev
