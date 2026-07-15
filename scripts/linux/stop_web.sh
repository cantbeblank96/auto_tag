#!/usr/bin/env bash
# 一键关闭 Web 控制台（后端 + 前端）
# 用法：bash scripts/linux/stop_web.sh

set -euo pipefail

BACKEND_PORT="${PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-5020}"

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

stop_port() {
  local port="$1"
  if ! port_in_use "$port"; then
    echo "  端口 ${port} 当前无监听进程"
    return 0
  fi
  if command -v fuser >/dev/null 2>&1; then
    fuser -k "${port}/tcp" >/dev/null 2>&1 || true
    echo "  已结束占用端口 ${port} 的进程"
  else
    echo "  未找到 fuser，无法结束端口 ${port} 上的进程" >&2
    return 1
  fi
}

echo "==> 正在关闭 Auto Tag Web 服务..."
echo "    后端端口: ${BACKEND_PORT}"
echo "    前端端口: ${FRONTEND_PORT}"
stop_port "$BACKEND_PORT"
stop_port "$FRONTEND_PORT"
sleep 0.4
echo "==> 关闭完成。"
