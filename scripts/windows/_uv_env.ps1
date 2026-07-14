# 由 scripts/windows/*.ps1 点源；解析仓库根目录与 .venv Python。
# 可覆盖：$env:PYTHON_EXECUTABLE、$env:VENV_DIR

$ErrorActionPreference = "Stop"

if (-not $ScriptDir) {
    $ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
}
$RepoRoot = (Resolve-Path (Join-Path $ScriptDir "..\..")).Path
$VenvDir = if ($env:VENV_DIR) { $env:VENV_DIR } else { Join-Path $RepoRoot ".venv" }
$PythonExec = if ($env:PYTHON_EXECUTABLE) { $env:PYTHON_EXECUTABLE } else { Join-Path $VenvDir "Scripts\python.exe" }

if (-not (Test-Path -LiteralPath $PythonExec)) {
    Write-Error "未找到虚拟环境：$PythonExec`n请先执行：powershell -File scripts/windows/setup_uv_env.ps1"
}

if (-not $env:PYTHONPATH) {
    $env:PYTHONPATH = $RepoRoot
} elseif ($env:PYTHONPATH -notlike "*$RepoRoot*") {
    $env:PYTHONPATH = "$RepoRoot;$env:PYTHONPATH"
}

# 确保本机 uv 安装目录在 PATH（供其它脚本复用）
$localBin = Join-Path $env:USERPROFILE ".local\bin"
if ((Test-Path -LiteralPath $localBin) -and ($env:PATH -notlike "*$localBin*")) {
    $env:PATH = "$localBin;$env:PATH"
}
