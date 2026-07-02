# 启动 FastAPI 后端
# 用法：powershell -ExecutionPolicy Bypass -File scripts/windows/run_web_backend.ps1

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $ScriptDir "_uv_env.ps1")

$HostAddr = if ($env:HOST) { $env:HOST } else { "0.0.0.0" }
$Port = if ($env:PORT) { $env:PORT } else { "8000" }

Set-Location $RepoRoot
& $PythonExec -m uvicorn auto_tag.backend.app:app --host $HostAddr --port $Port
