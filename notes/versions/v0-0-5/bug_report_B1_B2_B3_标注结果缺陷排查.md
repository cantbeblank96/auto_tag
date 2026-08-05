# v0.0.5 Bug 排查报告：B1 / B2 / B3 标注结果缺陷

> 背景：v0.0.4 评测（`results/v0.0.4测试结果及改进方向.md`）中发现三类标注结果缺陷。
> 本文记录每个 bug 的起因、分析、定位过程、最终解决方案与经验总结。
> 配套 feature 文档见同目录 `feature_vlm_max_tokens_自动查询.md`。

## 问题总览

| 编号 | 现象 | 表面原因 | 真实根因 |
|------|------|----------|----------|
| B1 | 最后一个 label 字段恒为空 | questions schema 读入时末项丢失 | config.json 读取层 int 键转换破坏了数据结构 |
| B2 | 偶发空标签（`labels_json = {}`）落盘且状态为 done | VLM 空响应被放行 | 异步打标池对空结果无硬拦截，且缺收尾兜底 |
| B3 | 非法枚举值（如 `slight_yield`）落盘 | 模型「三次纠错都纠不过来」 | **thinking 模型的 reasoning 与 content 共用 max_tokens 预算，4096 不够导致输出被截断成残缺 JSON** |

三个 bug 最终在 24 维 schema 下做了清库端到端复现验证：全库 1419 条记录全部 done、0 非法值、0 空标签、96 个簇标签一致。

配套的两个新 feature（max_tokens 按模型配置 + 自动查询、chain dump 配置项）见同目录 `feature_*.md`。

---

## B1：最后一个 label 字段为空

### 起因

评测发现每条标注结果的最后一个字段总是缺失/为空。

### 分析与定位

- 最初怀疑是 prompt 拼接或模型输出截断，但 dump 显示模型其实返回了完整字段。
- 逐层回退后发现：`config.json` 中的 `questions` 在**读入**阶段就少了一个条目——问题不在 VLM 链路，而在配置加载。
- 根因：旧的 JSON 读取方式在类型转换时会破坏数据结构（int 键转换问题），导致 schema 末项丢失，prompt 里自然没有该字段，模型也就不会输出。

### 解决方案

- 统一改用 `kevin_toolbox.data_flow.file.json_` 读写 JSON（`b_use_suggested_converter=True`），保证 questions 结构完整读入。
- 前端「Questions 管理」现在能正确展示全部 24 维；保存写盘后再读回验证字段无损。

### 经验

- 「结果少一个字段」这类问题，先验证**输入侧 schema 是否完整**，再怀疑模型输出，能少走很多弯路。

---

## B2：偶发空标签落盘

### 起因

库中偶发出现 `labels_json = "{}"` 但 `annotation_status = "done"` 的记录。

### 分析与定位

- 打标走的是生产者-消费者模型：`cluster_engine` 建簇后，`vlm_annotation_pool` 全局 worker 池异步打标并回写中心标签。
- 两处缺口：
  1. VLM 返回空 dict 时没有拦截，被当作「成功」以 done 状态落盘；
  2. 任务结束时没有收尾兜底，中途失败/竞态留下的半成品无人补救。

### 解决方案

- **落盘硬拦截**（`vlm_annotation_pool.py`）：worker 拿到结果后，空 dict 直接抛错，`_mark_center_failed` 将该簇中心标记为 `failed`，等待重标，绝不以 done 落盘。
- **收尾兜底**：流水线末尾新增 `backfill_pending_labels`（`vlm_annotation_pool.py` / `pipeline.py`），扫描 failed/pending 记录并回填，把漏网之鱼补上。

### 经验

- 异步池的每条「成功路径」都要问一句：**空结果/异常结果会不会被当成成功落盘？**落盘前的最后一道硬校验比上游任何重试都可靠。

---

## B3：非法枚举值落盘（重点）

### 起因

评测发现库中出现不在 choices 里的枚举值（如 `slight_yield`，正确值应为 `slight_yaw`）。

### 第一层：补校验缺口（防御修复）

起初判断是校验链存在双缺口：

1. VLM 多轮纠正（`vlm_validation_max_corrections`）对枚举取值的约束不够硬；
2. 落盘前没有对 questions 做最终形式校验。

修复（均为防御性硬拦截）：

- `vlm_client.py` 新增 `VLMValidationError`：纠正轮数用尽仍非法时抛异常触发 failover / 标记失败，**绝不落盘非法值**；纠正轮中出现空响应同样不放行（上一轮输出已知非法）。
- `vlm_annotation_pool.py` 落盘前调用 `VLMClient.validate_against_questions` 做最终校验，非法即标记 failed。

### 第二层：用户的质疑与真实根因

用户提出关键质疑：*「模型应该没有这么傻，不会真的三次纠错都纠不过来。」*

带着这个质疑做了干净的端到端复现：清空数据库 → questions 切到 24 维 → 重启后端 → 跑测试任务。结果仍有 2 个簇中心失败。于是给这 2 个失败样本专门开诊断日志：

**取证工具**：`_dump_validation_chain` —— 校验链最终失败时，把完整对话链（system/user prompt、每轮 assistant 输出、纠正 prompt，图片 base64 已去除）追写到 `logs/vlm_validation_chain.jsonl`，并带上 `finish_reason`。（该转储现已做成正式配置项 `vlm_chain_dump`，见同目录 `feature_vlm_chain_dump_校验链转储配置项.md`）

**dump 分析发现**：三轮 assistant 输出全部在 ~250–480 字符处戛然而止，是**残缺 JSON**（错误为 `Unterminated string` / `Expecting property name enclosed in double quotes`），而不是模型坚持输出错误的枚举值。也就是说：**模型不是「纠不过来」，而是输出根本没写完**。

**端点探测确认根因**：用低层 httpx 直接打 sensenova `/v1/chat/completions`，逐步调 `max_tokens`：

| max_tokens | 现象 |
|------------|------|
| 300 | `finish_reason='length'`；message 里**只有 `role` + `reasoning`，没有 `content` 键** |
| 1500 | `finish_reason='length'`，content 仍被截断 |
| 4096（旧默认） | 边界竞态：schema 大、思考多时仍会截断 |
| 8192+ | 正常完整输出 |

结论：**sensenova-6.7-flash-lite 是 thinking 模型，`reasoning`（思考过程）与 `content`（答案）共用同一个 `max_tokens` 预算**。24 维 schema 的 prompt 较大，模型的思考消耗掉大部分预算后，真正的答案 JSON 写到一半就被截断。预算耗尽时甚至可能整个 content 都不返回。

### 解决方案

1. **`vlm_max_tokens` 可配置**（config.json / Settings）：默认给足预算（修复时验证值为 8192），thinking 模型不再截断。后续进一步做成「留空自动查询模型上限」，见 feature 文档。
2. **dump 记录 `finish_reason`**：截断（`length`）从此一眼可辨，是本次定位的关键证据，固化进诊断工具。
3. **reannotate 修复闭环**（`database_maintenance.py`）：重标成功后将中心置为 `done`，并把标签**传播给同簇 `labels_json=="{}"` 的成员**（返回 `propagated_to_members` 计数），避免中心修好而成员仍是空标签。

### 验证

对 2 个失败簇执行 reannotate：`errors=0`、`propagated_to_members=5`。最终全库一致性检查：**1419 条 done、0 非法值、0 空标签、96 个簇内标签一致**。

### 经验

1. **不要轻信「模型很傻」的结论**。连续三轮纠错失败这种反直觉现象，背后往往是链路问题（截断、编码、解析），先取证再下结论。
2. **finish_reason 是 LLM 集成的第一现场证据**。`length` 与 `stop` 的含义差异能直接区分「模型不配合」和「预算不够」。
3. **thinking 模型的 token 预算机制是隐性坑**：reasoning 与 content 共享 max_tokens，且预算耗尽时部分端点会直接不返回 content。对接任何 thinking 模型（sensenova、DeepSeek-R1、o 系列等）都要预留充足预算，或支持自动查询模型上限。
4. **失败现场转储（dump）值得常备**。本次能定位到根因，完全靠 `vlm_validation_chain.jsonl` 里保留的完整对话链；没有它只能看到一句「解析失败」。

---

## 整体经验总结

- **硬拦截 > 软纠正**：非法/空结果宁可标记 failed 等待重标，也绝不允许以任何形式落盘；落盘前最后一道校验必须独立于上游所有逻辑。
- **复现要干净**：清库 + 重启后端（config 是启动时加载进内存的）+ 固定 schema，否则旧数据会污染结论（本次一开始误以为 B3 没复现，其实是库里旧数据在干扰）。
- **诊断先行，修复随后**：先加 dump / 直接探测端点拿到证据，再改代码；两轮修复（防御性硬拦截 + 根因修复 max_tokens）分开提交、分开验证。
- **收尾兜底不可少**：异步流水线必须有任务结束时的补偿步骤（`backfill_pending_labels`、reannotate 传播簇成员），处理中途失败的中间状态。
