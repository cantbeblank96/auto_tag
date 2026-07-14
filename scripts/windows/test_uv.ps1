# 测试本机 uv 是否可用。
# 用法：powershell -ExecutionPolicy Bypass -File scripts/windows/test_uv.ps1

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# 补 PATH（不调用 ensure_uv.ps1，避免其 exit 结束当前脚本）
foreach ($bin in @(
    (Join-Path $env:USERPROFILE ".local\bin"),
    (Join-Path $env:USERPROFILE ".cargo\bin")
)) {
    if ((Test-Path -LiteralPath $bin) -and ($env:PATH -notlike "*$bin*")) {
        $env:PATH = "$bin;$env:PATH"
    }
}

$uv = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uv) {
    if ($env:AUTO_INSTALL_UV -eq "1") {
        & (Join-Path $ScriptDir "ensure_uv.ps1")
        foreach ($bin in @(
            (Join-Path $env:USERPROFILE ".local\bin"),
            (Join-Path $env:USERPROFILE ".cargo\bin")
        )) {
            if ((Test-Path -LiteralPath $bin) -and ($env:PATH -notlike "*$bin*")) {
                $env:PATH = "$bin;$env:PATH"
            }
        }
        $uv = Get-Command uv -ErrorAction SilentlyContinue
    }
    if (-not $uv) {
        Write-Error "失败：未找到 uv。运行 scripts/windows/ensure_uv.ps1 安装，或设置 AUTO_INSTALL_UV=1。"
    }
}

Write-Host "==> uv 版本"
uv --version

Write-Host "==> 可用 Python（节选）"
try {
    uv python list | Select-Object -First 5
} catch {
    Write-Host "(uv python list 跳过)"
}

Write-Host "==> uv 自检通过"
