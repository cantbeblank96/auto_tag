# 标注工具注入（annotation tools）

> 标注时按需运行客观测量工具（kevin_sdk），把结果以**纯文本 JSON**（及可选的**裁剪图片**）注入 VLM 消息，
> 辅助 VLM 回答相关问题。最终标签仍由 VLM 综合图片与测量信息判断，**工具不做硬覆盖**。
> 版本：v0.0.6 引入；详细演进记录见 `notes/versions/v0-0-6/feature_annotation_tools_标注工具注入.md`。

## 1. 整体流程

```
设置页定义 questions
   │  「智能分析工具」按钮（POST /api/annotation_tools/analyze）
   │  → VLM agent 按问题语义建议工具绑定，人工可改
   ▼
questions[key].tools（问题级绑定） ∩ annotation_tools.<name>.enabled（全局开关）
   │  = 本次标注实际执行的工具（vlm_client._collect_tools）
   ▼
run_tools_for_image(image, tools)          # auto_tag/core/annotation_tools.py
   │  detect 结果同批共享；主脸关键点在 head_pose/face_attribute 间复用
   ▼
{tool_name: 结果 dict}（可含 _images 键）
   ▼
_messages_with_image(..., tool_results=…)  # auto_tag/core/vlm_client.py
   │  JSON 文本块 + _images 渲染为独立图片块
   ▼
VLM 输出标签（工具信息仅作 factual reference）
```

## 2. 已注册工具

注册表：`auto_tag/core/annotation_tools.py` 的 `TOOL_REGISTRY`。

| 工具 | 输出 | 依赖（requires_model / requires_align） |
|---|---|---|
| `face_detect` | 人脸数、最大脸 bbox / height_ratio / area_ratio / score | detect 模型 / 否 |
| `head_pose` | yaw / pitch / roll（度） | pose 模型 / 是 |
| `face_attribute` | Gender / Age / Race 等属性 | attribute 模型 / 是 |
| `face_crop` | 前 N 大脸的转正裁剪图（**图片注入**） | 无自有模型（复用 detect+align）/ 是 |

`tool_status(name)` 按 `requires_model` / `requires_align` 检查模型文件与
`align_model_paths`，产出 enabled / available / reason，供 API 与设置页展示。

## 3. 消息布局（KV cache 友好）

```
[0] 主 prompt（schema + tools_note 说明段）
[1] 工具段引导语
[2..] [Tool measurement: <tool>]\n{JSON}      ← 每工具一个文本块
      [Tool crop: face_crop face #k] 引导文本  ← face_crop 每张裁剪图前一块
      image_url（裁剪图）
[..] 收尾提醒 "The LAST image ..."
[-1] 待标注原图（必须殿后）
```

跨请求稳定前缀（prompt + 参考样图）在前，每请求不同的工具测量与待标注图在后，
提升推理侧 prefix/KV cache 命中率。参考样图段（`[Reference example: …]`）
插在 prompt 之后、工具段之前。

### `_images` 图片注入约定

- 工具结果 dict 可含 `_images` 键：base64 jpeg 字符串列表；
- `_messages_with_image` 渲染时先 `pop("_images")`，其余字段照常作 JSON 文本注入，
  `_images` 逐张渲染为「引导文本块 + image_url 块」；
- 该约定是通用的：任何新工具产出 `_images` 即自动获得图片注入能力，无需改 vlm_client。

### face_crop 细节

- 选脸：`_detect_faces_sorted` 全部检出（过滤 `face_detect.min_score`），
  按 `max(bbox_w, bbox_h)` 降序，取前 `annotation_tools.face_crop.max_faces`（默认 2）；
- 裁剪：每脸独立 `align`（360align + align）→ `crop_image_by_landmarks`，
  输出 **178×218（宽×高）** 转正标准人脸图（BGR → RGB → JPEG base64，单张约 6 KB）；
  SDK 日志中打印的 `crop_size:72,72` 是 C 库内部中间量，**不是输出尺寸**；
- 单脸裁剪失败仅跳过该脸，不影响其余脸与其余工具；无人脸返回
  `{"face_count": 0, "cropped_faces": 0, ...}`。

## 4. 关键实现位置

| 文件 | 职责 |
|---|---|
| `auto_tag/core/annotation_tools.py` | `TOOL_REGISTRY`、模型句柄懒加载单例、全局锁串行 SDK 调用、`tool_status()` / `enabled_tool_names()` / `run_tools_for_image()` |
| `auto_tag/core/vlm_client.py` | `_collect_tools(keys)`、`_messages_with_image(tool_results=…)`、`_generate_prompt*(with_tools_note=…)`、`resolve_max_tokens_for_model()` |
| `auto_tag/backend/routers/annotation_tools.py` | `GET /api/annotation_tools`（状态）、`POST /analyze`（VLM agent 建议绑定） |
| `auto_tag/core/config.py` | `annotation_tools` dict 字段（热重载白名单） |
| `auto_tag/web/src/pages/Settings.tsx` | 工具管理章节、问题级 tools 多选、智能分析按钮、face_crop `max_faces` 输入框 |

容错约定：

- 单工具失败记 `{"error": …}`，不影响其他工具；
- 工具执行整体异常 → 降级为无工具标注（不阻塞任务）；
- 全局停用某工具时，保存自动从所有问题绑定中剔除（前端过滤 + 运行时交集双保险）。

## 5. 配置（config.json → annotation_tools）

```jsonc
{
  "annotation_tools": {
    "align_model_paths": ["<360align 模型>", "<align 模型>"],   // requires_align 工具共用
    "face_detect":    { "enabled": true, "model_path": "...", "min_score": 0.5 },
    "head_pose":      { "enabled": true, "model_path": "..." },
    "face_attribute": { "enabled": true, "model_path": "..." },
    "face_crop":      { "enabled": true, "max_faces": 2 }      // 无 model_path
  }
}
```

analyze 接口的 max_tokens 与标注链路一致，统一走
`vlm_client.resolve_max_tokens_for_model(model)`：
模型条目 `max_tokens` > 全局 `vlm_max_tokens` > 自动查询模型上限
（thinking 模型 reasoning 与 content 共用预算，防截断）。

### 样图工具测量（可选增强，默认关）

`annotation_tools_on_examples: true` 时，对 questions 的 examples 参考样图也执行其
**该维度绑定**的工具（绑定 ∩ 全局可用，与待标注图同口径），测量结果紧随对应样图
之后注入：`[Reference measurement: <tool>]` JSON + `[Reference crop: ...]` 裁剪图，
供模型与待标注图的工具结果对比校准刻度。实现要点（vlm_client）：

- `_example_tool_results`：结果按 (路径, mtime, max_side, 工具签名) 常驻缓存，
  样图为跨请求静态内容，只跑一次；纯失败条目（仅含 error 键）剔除，全失败静默降级为仅注入样图；
- `_collect_examples` 返回四元组 (维度, 档位值, base64, 工具结果|None)，开关关闭时第四元素恒 None；
- 开关在 Settings 页通用设置区暴露，保存后经 `reload_settings_from_disk` 免重启生效。

## 6. 新增一个工具

1. `TOOL_REGISTRY` 追加条目：`name / display_name / description`（描述同时供
   VLM agent 分析与前端展示，写清适用问题类型）`/ requires_align / requires_model`；
2. 在 `run_tools_for_image` 的循环中加分支（`with _SDK_LOCK:` 内）：
   - 需要自有模型：`_build_handle` 懒加载句柄，并在 `tool_status` 的
     `model_path` 检查自然生效（`requires_model: true`）；
   - 产出图片：结果 dict 里放 `_images`（base64 列表）即可，渲染自动完成；
3. `config.json` / `config.example.json` 加 `"<name>": {"enabled": …, …}` 段
   （config.py 的 `annotation_tools` 是整 dict 字段，无需改 schema）；
4. 如有特殊参数（如 `max_faces`），在设置页工具卡片加输入框
   （参考 Settings.tsx 中 `t.name === 'face_crop'` 分支）。

注册完成后「智能分析工具」会自动把新工具纳入 agent 的候选集，无需改 analyze 逻辑。

## 7. 已知局限

- 旋转图的 head_pose 角度按图像坐标计算（如 roll≈90），靠 prompt 提示 VLM 结合图片解读；
- 工具执行每请求一次，未做跨请求按图缓存（detect/align 毫秒级，暂无瓶颈）；
- `eye_state` 未注册：kevin_sdk 上游标注「TODO 有 bug，未跑通」，修复后再加。
