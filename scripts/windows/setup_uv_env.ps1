# 使用 uv 创建 .venv 并安装 Python 依赖。
# 用法：powershell -ExecutionPolicy Bypass -File scripts/windows/setup_uv_env.ps1
# 可选环境变量：VENV_DIR / PYTHON_VERSION / INSTALL_WEB=0 / WITH_NPM=1 / UV_LINK_MODE

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = (Resolve-Path (Join-Path $ScriptDir "..\..")).Path
$VenvDir = if ($env:VENV_DIR) { $env:VENV_DIR } else { Join-Path $RepoRoot ".venv" }
$PythonVersion = if ($env:PYTHON_VERSION) { $env:PYTHON_VERSION } else { "3.11" }

Set-Location $RepoRoot

& (Join-Path $ScriptDir "ensure_uv.ps1")

Set-Location $RepoRoot

# Windows 上项目与 uv cache 常跨盘符，hardlink 会失败；默认用 copy（可用 UV_LINK_MODE 覆盖）
if (-not $env:UV_LINK_MODE) {
    $env:UV_LINK_MODE = "copy"
}

$syncArgs = @("sync", "--python", $PythonVersion)
if ($env:INSTALL_WEB -ne "0") {
    $syncArgs += "--extra", "web"
}

Write-Host "==> uv sync（依据 pyproject.toml / uv.lock）"
if ($env:VENV_DIR -and $env:VENV_DIR -ne (Join-Path $RepoRoot ".venv")) {
    $env:UV_PROJECT_ENVIRONMENT = $env:VENV_DIR
}
uv @syncArgs
$VenvDir = if ($env:VENV_DIR) { $env:VENV_DIR } else { Join-Path $RepoRoot ".venv" }

$env:PYTHONPATH = $RepoRoot
$PythonExec = Join-Path $VenvDir "Scripts\python.exe"

if ($env:WITH_NPM -eq "1") {
    if (Get-Command npm -ErrorAction SilentlyContinue) {
        Write-Host "==> npm install (auto_tag/web)"
        Push-Location (Join-Path $RepoRoot "auto_tag\web")
        npm install
        Pop-Location
    } else {
        Write-Warning "未找到 npm，跳过前端依赖安装"
    }
}

Write-Host ""
Write-Host "完成。激活环境："
Write-Host "  $VenvDir\Scripts\Activate.ps1"
Write-Host "  `$env:PYTHONPATH = '$RepoRoot'"
Write-Host ""
Write-Host "或直接使用 scripts/windows/run_*.ps1。"
