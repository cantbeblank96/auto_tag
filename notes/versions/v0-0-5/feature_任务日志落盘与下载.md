# v0.0.5 Feature：任务日志落盘与下载（失败定位）

> 背景：任务表中「失败N」只是一个数字。早期任务日志只存在 job_runner 的内存 deque 里，
> 后端一重启就丢；root logger 也没挂任务级 FileHandler，`logs/auto_tag_web_backend.log`
> 里只有零散 WARNING。用户/工程师看到失败数后，无法回答「到底为什么失败」。
> 本 feature 让每个任务的完整日志落盘、可下载，并增强 VLM HTTP 错误的日志内容。

## 一、任务级日志落盘

任务运行时（`job_runner.submit_job().run()`），除原有内存 deque（maxlen=8000）外，
给 root logger 挂一个任务级 `FileHandler`：

- 落盘路径：`{work_dir}/log/jobs/job_{job_id}.log`（由 `job_log_file(job_id)` 计算）；
- 级别 INFO、格式与内存日志一致（`%(asctime)s [%(levelname)s] %(name)s - %(message)s`）；
- 任务结束（finally）时移除并关闭 handler，不影响后续任务。

读取侧 `get_job_logs()`：内存 deque 非空时优先用内存；为空（后端重启后）回退读落盘文件，
支持 `tail` 参数。

## 二、下载端点与前端按钮

| 层 | 内容 |
|----|------|
| API | `GET /api/jobs/{job_id}/logs/download` → `FileResponse`（`job_{8位id}.log`）；文件不存在返回 404 + 友好提示 |
| 前端 | `client.ts` 新增 `downloadJobLog(jobId)`；任务表「失败N」红字旁显示 ⬇ 小圆点按钮，点击即下载 |

历史任务（v0.0.5 之前提交、无落盘文件）点击下载会得到明确提示，而不是静默失败。

## 三、VLM HTTP 错误日志增强

此前 `HTTPStatusError` 经 tenacity RetryError 包装后，日志只剩异常类名，看不到状态码与
服务端返回内容。两处增强（`auto_tag/core/vlm_client.py`）：

1. `openai_chat_completion` 中 `raise_for_status()` 失败时重写 `e.args`，附带
   `HTTP {status} from {url}: {响应体前300字符}`——选择改 args 而非 `raise ... from`，
   保证 failover 链上所有打印点都能看到详情；
2. `_chat_raw` 的重试日志从只打类名改为
   `VLM HTTP call failed after {elapsed}s: {异常类名}: {前300字符}`。

## 四、排查记录（为什么需要这个 feature）

- 任务 `21acb605`（失败 1）：内存日志已丢，只能靠 chain dump 还原——sensenova-6.7-flash-lite
  对同一张图三轮输出分别在第 244/479 字符处中途停止（`finish_reason=None`，非 max_tokens
  截断），最终 `unparseable_after_corrections`；
- 清库重跑任务 `9e388635`（1982 张，失败 1）：落盘日志完整捕获失败链——`VLM API network error`
  重试 3 次 → 两个端点均 `HTTPStatusError` → `All models failed`。本次 chain dump 无新记录，
  证明该失败与 JSON 截断无关；
- 结论：该端点存在多种不稳定形态（HTTP 错误 / ReadTimeout / 空响应 / 中途停止输出），
  失败率 1-2%，属于服务端偶发问题；可观测性（落盘日志 + 下载）是定位此类问题的前提。

## 验证记录

- 小任务落盘成功；`GET .../logs/download` 返回 200，`content-disposition: attachment`；
- 前端 ⬇ 按钮显示正常；历史任务点击下载返回 404 友好提示；
- 后端重启后 `/api/jobs/{id}/logs` 仍可从磁盘回退读到日志。
