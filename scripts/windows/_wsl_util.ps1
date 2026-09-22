# WSL 路径与后端脚本辅助（供 start_web_wsl / run_web_backend_wsl 等引用）

function Convert-ToWslPath([string]$WindowsPath) {
    if (-not $WindowsPath) { return $WindowsPath }
    $p = $WindowsPath.Replace('\', '/')
    if ($p -match '^([A-Za-z]):/(.*)$') {
        $drive = $Matches[1].ToLower()
        return "/mnt/$drive/$($Matches[2])"
    }
    return $p
}

function Get-WslBackendStartScript([string]$RepoRoot) {
    $wslRepo = Convert-ToWslPath $RepoRoot
    return "$wslRepo/scripts/linux/start_wsl_backend.sh"
}

function Stop-WslPort([int]$Port) {
    wsl -e bash -lc "fuser -k ${Port}/tcp 2>/dev/null || true" 2>$null | Out-Null
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

function Test-ListenPort([int]$Port) {
    $null -ne (
        Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
            Select-Object -First 1
    )
}

function Get-WslIpAddress() {
    $raw = (wsl hostname -I).Trim()
    if (-not $raw) {
        throw "Cannot resolve WSL IP (wsl hostname -I returned empty)."
    }
    return $raw.Split()[0]
}

function Get-WslBackendBaseUrl([int]$Port) {
    $wslIp = Get-WslIpAddress
    return "http://${wslIp}:$Port"
}
