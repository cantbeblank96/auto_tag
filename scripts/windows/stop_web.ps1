# 停止 Web 控制台（后端 8000 + 前端 5020）
# 用法：powershell -ExecutionPolicy Bypass -File scripts/windows/stop_web.ps1
# 或双击：stop_web.bat

$ErrorActionPreference = "SilentlyContinue"
$BackendPort = if ($env:PORT) { [int]$env:PORT } else { 8000 }
$FrontendPort = if ($env:FRONTEND_PORT) { [int]$env:FRONTEND_PORT } else { 5020 }

function Stop-ListenPort([int]$Port) {
    $pids = @(
        Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
            ForEach-Object { $_.OwningProcess }
    ) | Where-Object { $_ -and $_ -gt 0 } | Select-Object -Unique

    if ($pids.Count -eq 0) {
        Write-Host "  端口 $Port 当前无监听进程"
        return
    }

    foreach ($procId in $pids) {
        $p = Get-Process -Id $procId -ErrorAction SilentlyContinue
        $name = if ($p) { $p.ProcessName } else { "?" }
        Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
        Write-Host "  已结束占用端口 $Port 的进程 PID=$procId ($name)"
    }
}

Write-Host "==> 正在关闭 Auto Tag Web 服务..."
Write-Host "    后端端口: $BackendPort"
Write-Host "    前端端口: $FrontendPort"
Stop-ListenPort $BackendPort
Stop-ListenPort $FrontendPort
Start-Sleep -Milliseconds 400
Write-Host "==> 关闭完成。可关闭本窗口。"
