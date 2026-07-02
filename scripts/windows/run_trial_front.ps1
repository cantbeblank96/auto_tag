# 试采 Front 目录（请按本机路径修改 INPUT_DIR）
# 用法：powershell -ExecutionPolicy Bypass -File scripts/windows/run_trial_front.ps1

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $ScriptDir "_uv_env.ps1")

$InputDir = if ($env:INPUT_DIR) { $env:INPUT_DIR } else { "D:\data\Front" }
$WorkDir = if ($env:WORK_DIR) { $env:WORK_DIR } else { Join-Path $RepoRoot "run_work_front" }

Set-Location $RepoRoot
& $PythonExec -m auto_tag.main `
    --input_dir $InputDir `
    --work_dir $WorkDir `
    --b_skip_image_manually_verified `
    --b_mixed_yuv `
    --image_width 640 `
    --image_height 480 `
    --rotate_angle ROTATE_90_COUNTERCLOCKWISE
