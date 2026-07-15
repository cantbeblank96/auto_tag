#!/usr/bin/env bash
# 一键后台启动 Web 控制台（后端 + 前端）
# 用法：bash scripts/linux/start_web.sh
#
# 启动后浏览器打开：http://localhost:5020
# 日志默认写到：仓库根目录 logs/auto_tag_web_*.log

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/_uv_env.sh"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

BACKEND_PORT="${PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-5020}"
LOG_DIR="${REPO_ROOT}/logs"
mkdir -p "$LOG_DIR"
BACKEND_LOG="${AUTO_TAG_BACKEND_LOG:-${LOG_DIR}/auto_tag_web_backend.log}"
FRONTEND_LOG="${AUTO_TAG_FRONTEND_LOG:-${LOG_DIR}/auto_tag_web_frontend.log}"

port_in_use() {
  local port="$1"
  if command -v fuser >/dev/null 2>&1; then
    fuser "${port}/tcp" >/dev/null 2>&1
  elif command -v ss >/dev/null 2>&1; then
    ss -ltn "sport = :${port}" 2>/dev/null | grep -q LISTEN
  else
    return 1
  fi
}

wait_http_ok() {
  local url="$1"
  local timeout_sec="${2:-90}"
  local i=0
  while [ "$i" -lt "$timeout_sec" ]; do
    if curl -sf --max-time 3 "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
    i=$((i + 1))
  done
  return 1
}

start_detached() {
  local script_path="$1"
  local log_file="$2"
  # 脱离当前 terminal / SSH 会话，避免关窗即停
  nohup bash "$script_path" >>"$log_file" 2>&1 </dev/null &
  disown $! 2>/dev/null || true
}

if ! command -v curl >/dev/null 2>&1; then
  echo "未找到 curl，请先安装后再启动。" >&2
  exit 1
fi

if ! command -v node >/dev/null 2>&1 && ! command -v npm >/dev/null 2>&1; then
  echo "未找到 node/npm（Node.js）。请先安装 Node.js。" >&2
  exit 1
fi

echo "==> 启动 Auto Tag Web 控制台"
echo "    仓库目录: ${REPO_ROOT}"
echo "    后端日志: ${BACKEND_LOG}"
echo "    前端日志: ${FRONTEND_LOG}"

be_up=0
fe_up=0
port_in_use "$BACKEND_PORT" && be_up=1
port_in_use "$FRONTEND_PORT" && fe_up=1

if [ "$be_up" -eq 1 ] && [ "$fe_up" -eq 1 ]; then
  echo "==> 服务似乎已在运行。"
  echo "    请用浏览器打开: http://localhost:${FRONTEND_PORT}"
  echo "    若页面异常：bash scripts/linux/stop_web.sh && bash scripts/linux/start_web.sh"
  exit 0
fi

if [ "$be_up" -eq 0 ]; then
  echo "==> 启动后端 (端口 ${BACKEND_PORT}) ..."
  start_detached "$SCRIPT_DIR/run_web_backend.sh" "$BACKEND_LOG"
else
  echo "==> 后端端口 ${BACKEND_PORT} 已在监听，跳过启动"
fi

if [ "$fe_up" -eq 0 ]; then
  echo "==> 启动前端 (端口 ${FRONTEND_PORT}) ..."
  start_detached "$SCRIPT_DIR/run_web_frontend_v2.sh" "$FRONTEND_LOG"
else
  echo "==> 前端端口 ${FRONTEND_PORT} 已在监听，跳过启动"
fi

echo "==> 等待服务就绪（最多约 90 秒）..."
ok_be=0
ok_fe=0
wait_http_ok "http://127.0.0.1:${BACKEND_PORT}/api/health" 90 && ok_be=1
wait_http_ok "http://127.0.0.1:${FRONTEND_PORT}/" 90 && ok_fe=1

if [ "$ok_be" -eq 1 ] && [ "$ok_fe" -eq 1 ]; then
  echo ""
  echo "==> 启动成功！"
  echo "    请用浏览器打开控制台："
  echo "    http://localhost:${FRONTEND_PORT}"
  echo ""
  echo "    不用时请执行：bash scripts/linux/stop_web.sh"
  exit 0
fi

echo ""
echo "==> 启动未完全成功，请检查日志："
[ "$ok_be" -eq 0 ] && echo "    后端未就绪 -> ${BACKEND_LOG}"
[ "$ok_fe" -eq 0 ] && echo "    前端未就绪 -> ${FRONTEND_LOG}"
echo "    也可先 stop_web.sh，再重新 start_web.sh。"
exit 1
