#!/usr/bin/env bash
# 测试本机 uv 是否可用（存在、可执行、能列出 Python）。
# 用法：bash scripts/linux/test_uv.sh
# 未安装时 exit 1；AUTO_INSTALL_UV=1 时会先调用 ensure_uv.sh

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

export PATH="${HOME}/.local/bin:${PATH:-}"

if ! command -v uv >/dev/null 2>&1; then
  if [ "${AUTO_INSTALL_UV:-0}" = "1" ]; then
    bash "$SCRIPT_DIR/ensure_uv.sh"
  else
    echo "失败：未找到 uv。运行 bash scripts/linux/ensure_uv.sh 安装。" >&2
    exit 1
  fi
fi

echo "==> uv 版本"
uv --version

echo "==> 可用 Python（节选）"
uv python list 2>/dev/null | head -5 || true

echo "==> uv 自检通过"
