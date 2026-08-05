# v0.0.6 Feature：image-in-prompt —— 标签定义携带参考样图

> 背景：v0.0.4 评测（飞书文档《v0.0.4 测试结果及改进方向》）指出，「标尺类」标签
> （`background_brightness` 35.3%、`hairstyle` 62.0% 等）的核心问题是**人工标注按
> 「每档一张样图」对照打分，而 auto tag 只靠抽象文字描述主观估计**，导致系统性标尺偏移。
> 本 feature 落地文档 3.1 节的治本方案与 3.7 节的中长期方向：
> **让标签定义可携带参考样图，VLM 做视觉对照而非纯文字想象。**

## 用法（配置）

在 `config.json` 的任意 question 定义中新增可选字段 `examples`：**档位值 → 样图路径**的映射。

```json
{
    "questions": {
        "background_brightness": {
            "description": "背景亮度。0.0: ... ; 10.0: ...",
            "type": "float",
            "examples": {
                "2.5": "examples/bg_brightness_2.5.jpg",
                "5.0": "examples/bg_brightness_5.0.jpg",
                "7.5": "/abs/path/to/bg_brightness_7.5.jpg"
            }
        }
    },
    "vlm_example_image_max_side": 512,
    "vlm_image_max_side": 0
}
```

规则：

| 项 | 说明 |
|----|------|
| 路径解析 | 绝对路径直接用；相对路径基于 **config.json 所在目录** |
| 档位顺序 | 样图按档位值**数值升序**注入 prompt（非数值按字符串），保证标尺有序 |
| 样图缩放 | 加载后统一缩放到最长边 ≤ `vlm_example_image_max_side`（默认 512，范围 128–1024），JPEG q85 |
| 待标注图缩放 | 送入 VLM 前按 `vlm_image_max_side` 缩放最长边（只降不升）；**0 = 不缩放、原图发送**（默认）；前端「设置 → 通用」可调 |
| 缺图容错 | 样图文件缺失/损坏时**跳过该档并告警一次**（每路径去重），不阻断任务；该维度退化为纯文字标注 |
| 子集标注 | 增量/subset 标注只注入本次 keys 涉及维度的样图 |
| 无配置兼容 | 不写 `examples` 且 `vlm_image_max_side=0` 时行为与旧版完全一致（消息结构、prompt 文本均不变） |

## 实现要点（auto_tag/core/vlm_client.py）

1. **消息编排**（`_messages_with_image`）：首轮 user 消息的 content 数组为
   `主 prompt 文本 → 说明文本 → [Reference example: key = value] 文本 + 样图 × N → 收尾提醒文本 → 待标注图（最后一张）`。
   **固定前缀 + 可变尾部**：prompt 与样图跨请求完全相同，只有待标注图每请求不同；
   推理侧 prefix/KV cache 可命中前缀，大幅降低批量标注时的重复计算。收尾提醒强调
   「样图仅作标尺校准，消息中最后一张图才是待标注对象」。无样图时保持旧布局 `[text, 待标注图]` 不变。
2. **待标注图缩放**（`resize_image_for_vlm` + 配置项 `vlm_image_max_side`）：待标注图编码前按
   最长边缩放（只降不升），减小请求体积与推理耗时；已纳入 `reload_settings_from_disk`
   热重载白名单（顺带补上之前遗漏的 `vlm_example_image_max_side`）。
3. **prompt 说明段**（`_generate_prompt` / `_generate_prompt_for_keys` 新增 `with_examples_note`）：
   仅在实际附带样图时追加一段「样图在待标注图之前，对这些字段请对照校准标尺；最后一张图才是要标注的」说明；本地模型路径不追加（其不支持多图）。
4. **schema 占位**（`_prompt_schema_dict`）：写入 prompt 的 schema JSON 中，`examples`
   的文件路径被替换为占位说明——路径对模型无意义，避免污染上下文（所有生成/纠正 prompt 统一走此函数）。
5. **缓存**：样图 base64 以 `(绝对路径, mtime, max_side)` 为 key 常驻缓存（含加载失败的负缓存），
   千图任务只加载一次；缺失告警按路径去重，避免刷屏。
6. **纠正轮不受影响**：JSON/校验失败的文字追问轮沿用首轮会话（样图已在上下文），不重传图片。
   `vlm_chain_dump` 转储逻辑对多图自动兼容（所有 `image_url` 均省略 base64）。
7. **前端设置入口**（Settings.tsx 通用设置区）：新增 `vlm_image_max_side`（待标注图最长边，0=不缩放）
   与 `vlm_example_image_max_side`（参考样图最长边，128–1024）两个输入项，随保存写入 config.json。

## 验证（2026-08-05）

1. **单元级**：临时 config 注入 examples → 收集/排序/缓存/缺图跳过/相对与绝对路径解析/
   prompt 无路径泄漏/subset 过滤，全部断言通过。
2. **真实 VLM 端到端**：用 sensenova-6.7-flash-lite 对真实数据集图片做带样图标注
   （全量 24 维 + subset），请求被端点正常接受，输出通过 schema 校验；移除 examples
   回归后行为与旧版一致。
3. **Web 任务链路**：`config.json` 配置样图后重启后端，提交任务触发新建簇中心 VLM 标注，
   任务 done、标签 24 键完整落库（`vlm_calls=1, vlm_failed=0`）。
4. **样图前置重排 + 主图缩放回归**（2026-08-05 增补）：
   - 布局断言：无 examples 时 `[text, image]` 与旧版一致；有 examples 时样图全部在待标注图之前、
     待标注图为最后一个元素，全部通过；
   - `resize_image_for_vlm`：0/越界/非法值返回原图，缩放比例正确；
   - `vlm_image_max_side=100` 时消息中的待标注图确被缩放，`=0` 时保持原尺寸；
   - 真实 VLM 端到端（新布局）：全量 24 维带样图标注输出正常、schema 校验通过（E2E PASSED）；
   - 前端 `tsc --noEmit` 通过；服务已重启（8000/5020 健康）。

## 已知注意点

- **耗时上升**：多图请求处理时间显著变长。验证中 24 维 + 2 张样图的请求在 60s 超时边界，
  依赖现有 4 次指数退避重试兜底成功。建议配置样图的任务将 `vlm_http_timeout` 提到 **120**
  （本机 config.json 已调整）；样图张数多时可下调 `vlm_example_image_max_side` 控制体积，
  或设置 `vlm_image_max_side` 缩小待标注图。
- **KV cache 命中依赖前缀稳定**：样图/prompt 变更会使前缀失效；批量任务期间勿频繁改动 examples。
- **样图质量决定上限**：本 feature 提供的是「对照机制」，标尺是否对齐取决于样图是否与人工
  标注范式一致（每档一张典型样图）。

## 后续（本版本未做）

- detect 工具化（face_size / face_distance / face_brightness 用可计算量替代主观估计）：
  计划接入 `kevin_sdk`（人脸检测/属性/关键点）实现。
- head pose / gaze / beard / eye 专项模型方向见飞书文档 3.3–3.6 节。
