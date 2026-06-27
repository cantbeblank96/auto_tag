#!/usr/bin/env bash
# 试采 Front 目录：混合 JPG/NV21、640x480、逆时针 90°（与 notes 中示例一致）
# 用法：在任意目录执行 bash auto_tag/scripts/run_trial_front.sh
# 可覆盖：INPUT_DIR、WORK_DIR、PYTHON_EXECUTABLE（见 _conda_agent_d.sh）

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/_conda_agent_d.sh"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
export PYTHONPATH="$REPO_ROOT:${PYTHONPATH:-}"

INPUT_DIR="${INPUT_DIR:-/SDA/data_aqu_2024-12-26/试采/Pictures/Front}"
WORK_DIR="${WORK_DIR:-$REPO_ROOT/run_work_front}"

cd "$REPO_ROOT"
exec "$PYTHON_EXEC" -m auto_tag.main \
  --input_dir "$INPUT_DIR" \
  --work_dir "$WORK_DIR" \
  --b_skip_image_manually_verified \
  --b_mixed_yuv \
  --image_width 640 \
  --image_height 480 \
  --rotate_angle ROTATE_90_COUNTERCLOCKWISE
