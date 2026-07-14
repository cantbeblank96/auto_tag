# 检查系统是否已安装 uv；未安装时可选自动安装（官方 install.ps1）。
# 用法：powershell -ExecutionPolicy Bypass -File scripts/windows/ensure_uv.ps1
# 环境变量：$env:AUTO_INSTALL_UV = "0" 仅检查不安装

$ErrorActionPreference = "Stop"

function Add-UserLocalBinToPath {
    $localBin = Join-Path $env:USERPROFILE ".local\bin"
    if (Test-Path -LiteralPath $localBin) {
        if ($env:PATH -notlike "*$localBin*") {
            $env:PATH = "$localBin;$env:PATH"
        }
    }
    $cargoBin = Join-Path $env:USERPROFILE ".cargo\bin"
    if (Test-Path -LiteralPath $cargoBin) {
        if ($env:PATH -notlike "*$cargoBin*") {
            $env:PATH = "$cargoBin;$env:PATH"
        }
    }
}

function Install-Uv {
    Write-Host "==> 未检测到 uv，正在通过官方脚本安装..."
    irm https://astral.sh/uv/install.ps1 | iex
    Add-UserLocalBinToPath
}

Add-UserLocalBinToPath

$uv = Get-Command uv -ErrorAction SilentlyContinue
if ($uv) {
    Write-Host "uv 已安装：$(& uv --version)"
    return
}

if ($env:AUTO_INSTALL_UV -eq "0") {
    Write-Error "未找到 uv。可执行：powershell -File scripts/windows/ensure_uv.ps1"
}

Install-Uv
$uv = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uv) {
    Write-Error "安装后仍无法在 PATH 中找到 uv。请重新打开终端或将 uv 安装目录加入 PATH。"
}

Write-Host "uv 安装完成：$(& uv --version)"
