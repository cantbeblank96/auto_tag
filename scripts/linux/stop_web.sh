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
  # 收集当前占用 PID（fuser 优先，ss 兜底）
  local pids=""
  if command -v fuser >/dev/null 2>&1; then
    pids="$(fuser "${port}/tcp" 2>/dev/null | tr -s ' ' '\n' | grep -E '^[0-9]+$' | sort -u | tr '\n' ' ')"
    fuser -k "${port}/tcp" >/dev/null 2>&1 || true
  fi
  if [ -z "$pids" ] && command -v ss >/dev/null 2>&1; then
    pids="$(ss -tlnp "sport = :${port}" 2>/dev/null | grep -oE 'pid=[0-9]+' | cut -d= -f2 | sort -u | tr '\n' ' ')"
  fi
  # 验证端口是否已释放；未释放则逐个 kill -TERM，再不行 kill -KILL
  local i=0
  while port_in_use "$port" && [ "$i" -lt 10 ]; do
    for pid in $pids; do
      kill -TERM "$pid" >/dev/null 2>&1 || true
    done
    sleep 1
    i=$((i + 1))
    if port_in_use "$port" && [ "$i" -ge 5 ]; then
      for pid in $pids; do
        kill -KILL "$pid" >/dev/null 2>&1 || true
      done
    fi
  done
  if port_in_use "$port"; then
    echo "  端口 ${port} 结束失败，仍有进程占用（PID: ${pids:-未知}）" >&2
    return 1
  fi
  echo "  已结束占用端口 ${port} 的进程（已验证端口释放）"
}

echo "==> 正在关闭 Auto Tag Web 服务..."
echo "    后端端口: ${BACKEND_PORT}"
echo "    前端端口: ${FRONTEND_PORT}"
fail=0
stop_port "$BACKEND_PORT" || fail=1
stop_port "$FRONTEND_PORT" || fail=1
sleep 0.4
if [ "$fail" -ne 0 ]; then
  echo "==> 关闭未完全成功，请手动检查端口占用后再操作。" >&2
  exit 1
fi
echo "==> 关闭完成。"
