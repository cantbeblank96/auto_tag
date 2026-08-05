#!/usr/bin/env bash
# 启动 Vite 前端（v2）
# 用法：bash scripts/linux/run_web_frontend_v2.sh

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WEB_DIR="$(cd "$SCRIPT_DIR/../../auto_tag/web" && pwd)"

cd "$WEB_DIR"
echo "==> 启动 Vite 前端开发服务器 (http://localhost:5020)"
echo "    API 请求会代理到 http://localhost:8000"
# 默认改用轮询监听（CHOKIDAR_USEPOLLING），规避 inotify 实例数不足（max_user_instances）导致的 EMFILE 崩溃；
# 如环境 inotify 充裕，可设 CHOKIDAR_USEPOLLING=0 恢复原生监听
export CHOKIDAR_USEPOLLING="${CHOKIDAR_USEPOLLING:-1}"
export CHOKIDAR_INTERVAL="${CHOKIDAR_INTERVAL:-2000}"
exec npm run dev
