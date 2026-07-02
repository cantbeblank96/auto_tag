#!/usr/bin/env bash
# 启动 Vite 前端（v2）
# 用法：bash scripts/linux/run_web_frontend_v2.sh

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WEB_DIR="$(cd "$SCRIPT_DIR/../../auto_tag/web" && pwd)"

cd "$WEB_DIR"
echo "==> 启动 Vite 前端开发服务器 (http://localhost:5020)"
echo "    API 请求会代理到 http://localhost:8000"
exec npm run dev
