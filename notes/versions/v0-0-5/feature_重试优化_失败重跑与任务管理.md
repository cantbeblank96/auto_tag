# v0.0.5 Feature：重试策略优化、失败重跑与任务管理

> 背景：VLM 端点偶发不稳定（HTTP 错误 / 超时，失败率 1-2%）。用户提出三点改进：
> ① 重试次数提至 4 次并改用更明显的指数退让；② 任务表失败数旁增加「重跑失败部分」入口；
> ③ 任务页面「查询」章节升级为「管理」，支持多选删除与清空全部。

## 一、VLM 重试策略：4 次 + 指数退让（上限 60s）

`vlm_client._chat_raw` 的 tenacity 装饰器：

| 项 | 旧 | 新 |
|----|----|----|
| 尝试次数 | 3 | 4 |
| 等待 | `wait_exponential(multiplier=1, min=2, max=10)`（实际 2s/2s/4s，近似等间隔） | `wait_exponential(multiplier=2, min=2, max=60)`（2s → 4s → 8s，指数增长，上限 60s） |

实测任务日志可见 `retrying in 2.0s` → `retrying in 4.0s`，间隔随次数递增。

## 二、失败图片记录与「重跑失败部分」

### 失败列表的采集与落盘

- 两类失败都计入：加载/批处理失败（`result.failed_paths`）+ 簇中心 VLM 标注失败；
- 后者此前只计数不记路径：`on_vlm_failed` 回调签名改为 `Callable[[str], None]`
  （`vlm_annotation_pool` → `annotator` → `pipeline` 三层透传图片路径），
  pipeline 收集进 `PipelineResult.vlm_failed_paths`；
- 任务成功结束后，job_runner 将合并去重的失败列表写入
  `{work_dir}/log/jobs/job_{job_id}_failed.json`；
- 任务提交时的配置快照（`cfg_dict = asdict(cfg)`）一并持久化进
  `web_job_history.json`，供重跑时重建任务。

### 重跑 API：`POST /api/jobs/{job_id}/rerun_failed`

- 读失败列表 → 以 `cfg_dict` 重建 `PipelineConfig` → 将失败 JSON 作为 `image_ls` 输入
  （兼容旧格式 JSON 数组）、清空 `input_dirs`、强制 `skip_if_in_db=False`
  （失败的簇中心已在索引中，必须先删旧记录再重跑）→ `submit_job` 返回新任务 ID；
- 错误映射：无失败记录 → 404；无配置快照（旧版本任务）→ 400；后端忙碌 → 409。

### 前端

任务表「失败N」旁按钮顺序：⬇（下载日志）→ ↻（重跑失败部分，琥珀色小圆点）。
点击 ↻ 成功后提示「已提交重跑任务（失败 X 张），可在『管理』章节查看进度」。

## 三、「查询」→「管理」：多选删除 + 清空全部

- 章节标题更名为「管理」（`Tasks.tsx` 的 ChapterSection title）；
- `TaskQuerySection` 表格新增勾选列：表头全选（不含运行中/排队中），行级多选；
- 工具栏新增「删除所选 (N)」「清空全部」按钮，均先弹 `window.confirm` 二次确认
  （文案明确：仅删后端历史记录，不删 work_dir 下的日志/索引产物）；
- 后端：`DELETE /api/jobs`（body `{job_ids}`）→ `job_runner.delete_jobs`，
  运行中/排队中任务拒绝删除；内存 `_jobs` 与磁盘 `web_job_history.json` 同步删除
  （`job_store.delete_job_records`）。

## 验证记录

- 构造含损坏图片的测试任务：`job_..._failed.json` 正确落盘（仅损坏图）；
  `rerun_failed` 返回新任务，新任务 total=1、仅处理失败图；
- `DELETE /api/jobs` 正确返回 `deleted` / `missing`，历史文件同步更新；
- 浏览器验证：↻/⬇ 按钮显示；勾选后「删除所选 (1)」联动；删除/清空均弹确认框，
  取消后不删除；旧任务点 ↻ 提示「该任务无失败图片记录（或历史任务未落盘失败列表）」；
- 重试日志确认指数退避生效（2s → 4s）。

## 已知边界

- v0.0.5 之前提交的历史任务无 `cfg_dict` 与失败列表文件，重跑/下载会收到明确错误提示；
- 重跑产生的新任务若仍有个别失败，会再次落盘自己的失败列表，可继续链式重跑。
