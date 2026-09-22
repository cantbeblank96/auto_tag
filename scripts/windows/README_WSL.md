# Windows + WSL 启停说明（kevin_sdk 标注工具）

## 为什么需要 WSL 后端？

`kevin_sdk` 发行包内的原生库为 **Linux `.so`**，无法在 Windows 原生 Python（仓库 `.venv`）中加载。
若使用默认的 `start_web.bat` / `restart_web.bat`，后端跑在 Windows 上，**设置页标注工具会显示不可用**。

在已安装 WSL2（Ubuntu）的机器上，应使用本目录下带 **`_wsl`** 后缀的脚本：

| 脚本 | 作用 |
|------|------|
| `start_web_wsl.bat` / `start_web_wsl.ps1` | 后台启动 **WSL 后端** + **Windows 前端** |
| `restart_web_wsl.bat` / `restart_web_wsl.ps1` | 先关后开（推荐日常重启） |
| `run_web_backend_wsl.ps1` | 仅启动/重启 WSL 后端（8000） |
| `stop_web.bat` | 停止前后端（Windows 端口 8000/5020；WSL 8000 建议在 restart 时一并释放） |

原有 `start_web` / `restart_web` **保持不变**，适用于不需要 `kevin_sdk` 的场景。

## 首次安装 kevin_sdk（WSL 内，一次性）

在 **WSL** 终端执行（路径按实际仓库位置调整）：

```bash
cd /mnt/d/dev/kevin_auto_tag
bash scripts/linux/install_kevin_sdk_wsl.sh
```

前置条件：

- 同级目录存在 `kevin_sdk`（含 `release/*.whl` 与 `models/`）
- 证书已在 Windows 用户目录：`C:\Users\<你>\.kv_sdk_cfg\licenses.lic`
- `auto_tag/config.json` 中 `annotation_tools.*.model_path` 使用 **WSL 路径**，例如 `/mnt/d/dev/kevin_sdk/models/...`

WSL 专用虚拟环境默认：`~/.venvs/kevin_auto_tag_wsl`（与 Windows `.venv` 分离）。

## 日常启停

**PowerShell：**

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File D:\dev\kevin_auto_tag\scripts\windows\restart_web_wsl.ps1
```

**或双击：** `restart_web_wsl.bat`

浏览器打开：<http://localhost:5020>（必要时 Ctrl+F5）。

## 验收

```powershell
# WSL 后端（直接访问 WSL IP）
$wslIp = (wsl hostname -I).Trim().Split()[0]
curl.exe -s "http://${wslIp}:8000/api/health"
curl.exe -s "http://${wslIp}:8000/api/annotation_tools"

# 经 Vite 代理（与浏览器一致）
curl.exe -s http://127.0.0.1:5020/api/health
curl.exe -s http://127.0.0.1:5020/api/annotation_tools
```

`annotation_tools` 响应中 `face_detect` / `head_pose` 等应为 `"available": true`。

## 日志

| 组件 | 路径 |
|------|------|
| WSL 后端 | `{仓库}/logs/wsl_backend.log` |
| Windows 前端 | `%TEMP%\auto_tag_web_frontend.log` |

前端由 `start_web_wsl.ps1` 启动时会设置环境变量 **`AUTO_TAG_API_PROXY=http://<WSL-IP>:8000`**，
Vite 将 `/api` 代理到 WSL 后端（见 `auto_tag/web/vite.config.ts`）。  
因此浏览器访问 `http://localhost:5020` 即可正常使用 API；**不要**依赖 Windows 上的 `localhost:8000`。

## 常见问题

**Q：任务页提示「部分目录不存在」，但 Windows 下路径明明存在？**  
A：浏览器填的是 Windows 路径（如 `D:\dev\little_data\hunhe`），后端若跑在 WSL，需能访问对应挂载路径 `/mnt/d/dev/little_data/hunhe`。  
当前后端会自动把盘符路径规范为 `/mnt/<盘符>/...`；若仍报不存在，请确认目录已在 WSL 内可见（`ls /mnt/d/...`），且前端代理的是 WSL 后端（用 `restart_web_wsl`），而不是另一个占着 8000 的旧进程。

**Q：重启后标注工具又不可用？**  
A：是否误用了 `restart_web.bat`（Windows 原生后端）。请改用 `restart_web_wsl.bat`。

**Q：`WSL 后端启动失败`？**  
A：查看 `logs/wsl_backend.log`；确认 WSL 内已执行 `install_kevin_sdk_wsl.sh`，且 `~/.venvs/kevin_auto_tag_wsl` 存在。

**Q：前端 5020 打不开？**  
A：确认 Node.js 在 PATH 或设置 `NODE_DIR`；查看 `%TEMP%\auto_tag_web_frontend.log`。
