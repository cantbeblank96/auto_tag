# feature：标注工具注入（annotation tools，v0.0.6）

> 通用「工具注入」框架：标注时按需运行客观测量工具（kevin_sdk），把结果以**纯文本 JSON**（及可选的**裁剪图片**）注入 VLM prompt，
> 辅助 VLM 回答相关问题；最终标签仍由 VLM 综合图片与测量信息判断，**工具不做硬覆盖**。

## 背景与流程

1. 设置页 Questions 定义完成后，可用「**智能分析工具**」按钮：由 VLM（真 agent，使用 vlm_models 中第一个启用模型）
   分析每个问题需要哪些工具的客观信息（如 face_size 问题 → face_detect），给出建议绑定；
2. 建议在设置页人工可改：
   - 「**标注工具管理**」章节：显示已注册工具的名称/描述/模型路径/状态（可用/不可用+原因），**启用/停用**全局开关；
   - 「Questions 管理」每个问题卡片：**标注工具**多选框（候选 = 全局启用且可用的工具）；
3. 每次标注时：本次 keys 涉及问题绑定的工具 ∩ 全局可用工具 → 逐工具执行（同批共享 detect/关键点结果）→
   `[Tool measurement: <tool>] {JSON}` 文本段注入消息 → VLM 输出标签；
4. 全局停用某工具时，保存会自动从所有问题绑定中移除该工具（前端过滤 + 运行时交集双保险）。

## 已注册工具（kevin_sdk）

| 工具 | 说明 | 依赖 |
|------|------|------|
| `face_detect` | 人脸数量、最大人脸 bbox / height_ratio / area_ratio / score | detect 模型 |
| `head_pose` | yaw / pitch / roll（度） | detect + align 关键点 + pose 模型 |
| `face_attribute` | Gender / Age / Race 等属性分类 | detect + align 关键点 + attribute 模型 |
| `face_crop` | 按脸框最长边降序取前 N 脸（`max_faces` 配置，默认 2），逐脸 align + `crop_image_by_landmarks` 裁剪成转正人脸图，以**图片块**注入（`[Tool crop: face_crop face #k]`），供 VLM 重点看人脸细节 | detect + align 关键点（无自有模型，`requires_model: false`） |

> `eye_state` 暂不注册：kevin_sdk 上游标注「TODO 有 bug，未跑通」，待修复后再加。

## 消息布局（沿用 KV cache 友好编排）

```
prompt 文本 → [Reference example] 样图段 → [Tool measurement] 工具段（JSON 文本，
face_crop 额外附 [Tool crop: ...] 裁剪图块）→ 收尾提醒 → 待标注图（最后）
```

跨请求稳定前缀（prompt + 样图）在前，每请求不同的工具测量与待标注图在后。
prompt 附 tools_note 说明：工具测量仅作 factual reference，最终判断基于图片本身。

图片注入约定：工具结果 dict 可含 `_images` 键（base64 jpeg 列表）；`_messages_with_image`
渲染时将其 pop 出为独立图片块，其余字段照常作 JSON 文本注入。

## 关键实现

- `auto_tag/core/annotation_tools.py`：工具注册表 `TOOL_REGISTRY`（名称/描述/执行器/依赖）、
  模型句柄懒加载单例、全局锁串行 SDK 调用、`list_tool_status()` / `enabled_tool_names()` /
  `run_tools_for_image()`；单工具失败记 `{"error": ...}` 不影响其余工具；工具异常整体降级为无工具标注。
- `auto_tag/core/vlm_client.py`：`_collect_tools(keys)`（questions[key].tools ∩ 全局可用，注册顺序去重）、
  `_messages_with_image(tool_results=...)`、`_generate_prompt*(with_tools_note=...)`；
  四个标注入口统一走 `_annotate_via_conversation(tools=...)`。
- `auto_tag/backend/routers/annotation_tools.py`：
  - `GET /api/annotation_tools`：注册表 + enabled/available/reason + align_model_paths；
  - `POST /api/annotation_tools/analyze`：精简问题定义 + 工具描述送 VLM，返回 `{suggestions: {question_key: [tools]}}`。
    max_tokens 与标注链路一致，统一用 `vlm_client.resolve_max_tokens_for_model(model)`
    （模型条目 max_tokens > 全局 vlm_max_tokens > 自动查询模型上限），防 thinking 模型 reasoning 截断。
- `auto_tag/core/config.py`：`annotation_tools` dict 字段（含热重载白名单）；
  `config.json` → `annotation_tools`：各工具 `enabled/model_path`（face_detect 另有 `min_score`）+ `align_model_paths`。
- `auto_tag/web/src/pages/Settings.tsx`：工具管理章节、问题级 tools 多选、智能分析按钮；
  保存时按全局启用集合过滤问题绑定；face_crop 卡片提供「注入前 N 大人脸」max_faces 输入框（1~8）。

## 验证记录（2026-08-05）

- 单测（/tmp/ex_test/test_annotation_tools.py）：收集交集/停用过滤、消息布局、旧布局兼容、tools_note、状态 API 全过；
- face_crop 专项（/tmp/ex_test/test_face_crop_run.py + 多脸合成图）：单脸裁剪图质量正确（转正人脸）、
  双脸按最长边降序（109 > 58）、max_faces=1 截断正确、`_images` 渲染为独立图片块且 JSON 文本不含 `_images`、
  待标注图仍殿后；无人脸图优雅返回 face_count=0；
- 四工具真实执行（football_player.jpg）：detect score=0.97 height_ratio=0.14、pose/attribute 正常、face_crop 裁剪图 1 张；
- 智能分析真实 VLM（24 个问题）：face_size/face_count/face_distance→face_detect、head_pose/eye_contact→head_pose、
  gender/age/eyewear 等→face_attribute，其余空绑定，全部合理；
- Web E2E：绑定 tools 后提交单图任务，日志确认「标注工具注入: face_detect, head_pose, face_attribute, face_crop」，
  最终标签 gender=male、face_size=0（height_ratio 0.14 小脸）、head_pose=slight_yaw、face_count=1，与测量一致；
- 前端 tsc 通过；浏览器验证工具管理章节/智能分析按钮/问题级勾选状态均正确。

## 已知局限

- 旋转图 head_pose 原始角度按图像坐标计算（roll≈90），靠 prompt 提示 VLM 结合图片解读；后续可考虑注入前做姿态角归一；
- 工具执行目前每请求一次（未做跨请求按图缓存）；detect/align 毫秒级，暂无瓶颈；
- eye_state 待 kevin_sdk 修复后注册。
