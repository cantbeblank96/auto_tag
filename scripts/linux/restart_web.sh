#!/usr/bin/env bash
# 一键重启 Web 控制台（先关后开）
# 用法：bash scripts/linux/restart_web.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "==> 重启 Auto Tag Web：先关闭..."
bash "$SCRIPT_DIR/stop_web.sh"
sleep 1
echo ""
echo "==> 再启动..."
bash "$SCRIPT_DIR/start_web.sh"
