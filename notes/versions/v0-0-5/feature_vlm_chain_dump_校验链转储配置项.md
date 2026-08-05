# v0.0.5 Feature：vlm_chain_dump —— VLM 校验失败对话链转储正式配置项

> 背景：排查 B3 bug（见同目录 `bug_report_B1_B2_B3_标注结果缺陷排查.md`）时，靠校验链失败现场
> 转储 `vlm_validation_chain.jsonl` 定位到 thinking 模型输出截断的根因。此前该转储是「常开 +
> 环境变量改路径」的临时诊断手段，本版本将其做成正式配置项：**默认关闭，排查时一键开启**。

## 文件是干什么的

`vlm_validation_chain.jsonl` 是 VLM 校验链路的**失败现场转储**。当某张图片经过首轮标注和全部
纠正轮（`vlm_validation_max_corrections`）后仍校验失败时，`vlm_client._dump_validation_chain`
会把这次完整的对话链追写一条 JSONL 记录：

- 完整 `messages`（user schema prompt、每轮 assistant 输出、每轮纠正 prompt；图片 base64 已剔除，
  替换为 `<base64 omitted>` 占位，避免单条记录几十 MB）；
- `reason`（`invalid_after_corrections` / `unparseable_after_corrections`）；
- `finish_reason`（`length` = 输出被截断，是区分「模型不配合」与「预算不够」的关键证据）；
- `model` / `endpoint_id` / `errors` / `last_parsed` / `ts`。

B3 根因正是靠它定位的：没有它只能看到一句「JSON 解析失败」；有了它才能看到三轮输出都在
~400 字符处戛然而止，从而推断出是截断。

## 为什么要做成配置项

此前实现的三个问题：

1. **常开**：每次校验失败都写文件。排查期是利器，但生产长跑任务中属于轻微侵入——schema 写得
   不好导致大量失败时，文件会持续膨胀；
2. **路径控制不统一**：只能靠环境变量 `VLM_CHAIN_DUMP`，与项目其他参数（config.json + 设置页）
   的管理方式不一致；
3. **语义上它是诊断工具**：与 `pipeline_debug` 类似，理想形态是「默认关 / 排查时一键开」。

## 配置说明

| 配置键 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `vlm_chain_dump` | bool | `false` | 开关。开启后校验链最终失败时转储对话链 |
| `vlm_chain_dump_path` | string | `logs/vlm_validation_chain.jsonl` | 转储文件路径（追写） |

兼容性：**环境变量 `VLM_CHAIN_DUMP` 仍然存在时视为开启并覆盖路径**（旧用法不受影响）。

## 实现

- **`auto_tag/core/config.py`**：`Settings` 新增 `vlm_chain_dump` / `vlm_chain_dump_path` 两字段，
  `reload_settings_from_disk` 同步支持。
- **`auto_tag/core/vlm_client.py`**：`_dump_validation_chain` 开头检查开关——配置开启或
  `VLM_CHAIN_DUMP` 环境变量存在才写文件；路径取「环境变量 > 配置路径 > 默认值」。
- **`auto_tag/web/src/pages/Settings.tsx`**：「通用设置」新增勾选项
  「vlm_chain_dump（VLM 校验失败对话链转储，排查用）」，勾选后展开路径输入框。
- **`auto_tag/config.example.json`**：补充两个键的示例。

## 验证记录

- 开关关闭（默认）：调用 `_dump_validation_chain` 后文件大小不变；
- 开关开启 + 自定义路径：记录正确写入指定 JSONL，`reason` 字段完整；
- 设置页勾选 → 保存 → 磁盘 `vlm_chain_dump = true`；取消 → 保存 → 恢复 `false`，其余字段无损。

## 使用建议

- 日常跑任务保持关闭；
- 发现「非法值 / 空标签 / 解析失败」类问题时：设置页勾选开启 → 重启后端 → 小规模复现 →
  查看 JSONL 中的 `finish_reason` 与各轮输出定位根因 → 排查完关闭。
