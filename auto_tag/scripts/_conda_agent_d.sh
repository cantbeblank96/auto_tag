# 由同目录其它脚本 source；须已设置 SCRIPT_DIR。
# 约定：本项目脚本优先使用 conda 环境 agent_d（见仓库根目录 AGENTS.md）。
# 可显式指定：export PYTHON_EXECUTABLE=/path/to/python

if [ -n "${PYTHON_EXECUTABLE:-}" ] && [ -x "${PYTHON_EXECUTABLE}" ]; then
  PYTHON_EXEC="${PYTHON_EXECUTABLE}"
elif [ -x "${HOME}/anaconda3/envs/agent_d/bin/python" ]; then
  PYTHON_EXEC="${HOME}/anaconda3/envs/agent_d/bin/python"
elif [ -x "${HOME}/miniconda3/envs/agent_d/bin/python" ]; then
  PYTHON_EXEC="${HOME}/miniconda3/envs/agent_d/bin/python"
else
  PYTHON_EXEC="python"
fi
