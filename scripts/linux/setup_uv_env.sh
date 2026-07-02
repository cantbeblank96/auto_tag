#!/usr/bin/env bash
# 使用 uv 创建 .venv 并安装 Python 依赖。
# 用法：bash scripts/linux/setup_uv_env.sh
# 可选：INSTALL_WEB=0；WITH_NPM=1；AUTO_INSTALL_UV=0（不自动装 uv）

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
VENV_DIR="${VENV_DIR:-$REPO_ROOT/.venv}"
PYTHON_VERSION="${PYTHON_VERSION:-3.11}"

cd "$REPO_ROOT"

bash "$SCRIPT_DIR/ensure_uv.sh"
export PATH="${HOME}/.local/bin:${PATH:-}"

cd "$REPO_ROOT"

SYNC_ARGS=()
if [ "${INSTALL_WEB:-1}" != "0" ]; then
  SYNC_ARGS+=(--extra web)
fi

echo "==> uv sync（依据 pyproject.toml / uv.lock）"
if [ -n "${VENV_DIR:-}" ] && [ "$VENV_DIR" != "$REPO_ROOT/.venv" ]; then
  UV_PROJECT_ENVIRONMENT="$VENV_DIR" uv sync "${SYNC_ARGS[@]}" --python "${PYTHON_VERSION}"
else
  uv sync "${SYNC_ARGS[@]}" --python "${PYTHON_VERSION}"
  VENV_DIR="$REPO_ROOT/.venv"
fi

# shellcheck source=/dev/null
source "$VENV_DIR/bin/activate"
export PYTHONPATH="$REPO_ROOT:${PYTHONPATH:-}"

if [ "${WITH_NPM:-0}" = "1" ]; then
  if command -v npm >/dev/null 2>&1; then
    echo "==> npm install (auto_tag/web)"
    (cd auto_tag/web && npm install)
  else
    echo "警告：未找到 npm，跳过前端依赖安装" >&2
  fi
fi

echo ""
echo "完成。激活环境："
echo "  source $VENV_DIR/bin/activate"
echo "  export PYTHONPATH=\$PYTHONPATH:$REPO_ROOT"
echo ""
echo "或直接使用 scripts/linux/run_*.sh。"
