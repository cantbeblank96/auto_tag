# 由 scripts/linux/*.sh source；使用仓库根目录 .venv 中的 Python。
# 可覆盖：PYTHON_EXECUTABLE、VENV_DIR

if [ -z "${SCRIPT_DIR:-}" ]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
VENV_DIR="${VENV_DIR:-$REPO_ROOT/.venv}"

if [ -n "${PYTHON_EXECUTABLE:-}" ] && [ -x "${PYTHON_EXECUTABLE}" ]; then
  PYTHON_EXEC="${PYTHON_EXECUTABLE}"
elif [ -x "$VENV_DIR/bin/python" ]; then
  PYTHON_EXEC="$VENV_DIR/bin/python"
else
  echo "未找到虚拟环境：$VENV_DIR/bin/python" >&2
  echo "请先执行：bash scripts/linux/setup_uv_env.sh" >&2
  exit 1
fi

export PYTHONPATH="$REPO_ROOT:${PYTHONPATH:-}"
