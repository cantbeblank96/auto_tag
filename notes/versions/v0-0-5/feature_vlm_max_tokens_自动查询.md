# v0.0.5 Feature：vlm_max_tokens 按模型配置 + 留空自动查询模型最大输出长度

> 起因：B3 bug（见同目录 `bug_report_B1_B2_B3_标注结果缺陷排查.md`）的根因是 thinking 模型
> （sensenova-6.7-flash-lite）的 reasoning 与 content 共用 `max_tokens` 预算，旧默认值 4096 不够
> 导致输出被截断成残缺 JSON。为此把 `max_tokens` 做成正式配置项，并支持**留空时自动查询
> 该模型支持的最大输出长度**作为取值。
>
> v2 调整（2026-08-04）：配置粒度从全局改为**每个模型单独设置**；设置页在「自动」模式下
> 直接展示查询到的实际值。

## 功能说明

每个 VLM 模型条目可单独设置最大输出 tokens，行为是三态设计：

| 配置值 | 行为 |
|--------|------|
| 留空（`null` / 空串，**默认**） | 调用前自动 `GET {base_url}/models` 查询该模型支持的最大输出长度，用查询结果作为 `max_tokens`；设置页同步显示「自动：{查到的值}」 |
| 具体数值 | 该模型直接使用此值（clamp 到 `[1, 131072]`） |
| 自动查询失败（端点不支持 / 网络错误 / 条目无相关字段） | 回退到 **8192**；设置页显示「自动：8192（查询失败，回退默认）」（琥珀色提示） |

实际调用时的取值优先级（`vlm_client._chat_raw`）：

```
模型条目 max_tokens > 全局 settings.vlm_max_tokens（旧配置兼容） > 自动查询模型上限（回退 8192）
```

## 实现

### 后端

- **`auto_tag/core/vlm_client.py`**
  - `_lookup_model_max_output_tokens(model)`：请求 `GET {base_url}/models`（带该模型的 api_key，
    超时 10s），按 `id` / `name` 匹配条目，依次尝试 `max_output_length` → `max_output_tokens` →
    `max_tokens` 字段（不同供应商字段名不一致，做兼容）；结果 clamp 到 `[1, 131072]`。
  - `resolve_model_max_output_tokens(model)`：上面函数的带缓存包装。**线程安全缓存**
    `_MAX_OUTPUT_CACHE`（key 为 `(base_url, model_name)`）：成功结果常驻，失败结果缓存 5 分钟，
    避免每次调用都打 `/models`。
  - `_chat_raw` 按上述优先级取 `max_tokens`。
- **`auto_tag/backend/routers/models.py`**
  - 新增 `POST /api/models/resolve_max_output`：按请求体中的 `name` / `base_url` / `api_key`
    查询最大输出长度，返回 `{ok, value, source}`，`source=auto` 表示查到、`source=fallback`
    表示回退 8192。供设置页「自动」模式展示实际取值（表单未保存时也能查）。
- **`auto_tag/core/config.py`**：全局 `vlm_max_tokens`（`Optional[int]`）保留作为旧配置兼容的
  中间回退层，新配置推荐写在模型条目里。

实测：`GET https://token.sensenova.cn/v1/models` 中 `sensenova-6.7-flash-lite` 条目带
`max_output_length = 65536`，自动解析得到 65536（该端点实测接受 max_tokens 至少到 32768，
取模型声明上限留足余量）。

### 前端（设置页）

- **`auto_tag/web/src/pages/Settings.tsx`**：
  - 原「VLM 模型管理」工具条上的全局「最大输出 tokens」输入框**移除**；
  - 每个模型卡片内新增「最大输出 tokens」输入框（留空显示 placeholder「自动」）；
  - 留空时旁边显示后端查询到的实际值：「自动：65536」；查询失败时显示
    「自动：8192（查询失败，回退默认）」并以琥珀色标注；
  - 修改模型名称 / Base URL 后失焦自动重新查询。

### config.json

```json
{
  "vlm_models": [
    { "name": "sensenova-6.7-flash-lite", "max_tokens": null }
  ]
}
```

模型条目 `max_tokens` 为 `null`（或键缺失）= 该模型自动模式；需要限制时在条目里填数值。

## 验证记录

1. **后端单测**：`_lookup_model_max_output_tokens` 解析 sensenova 得 65536；`resolve_*` 第二次
   调用命中缓存；未知模型名走 8192 回退。
2. **API 实测**：`POST /api/models/resolve_max_output` 对 sensenova 返回
   `{"ok":true,"value":65536,"source":"auto"}`；对不存在的模型返回 `source=fallback, value=8192`。
3. **前端端到端**（playwright 实测 http://localhost:5020/settings）：
   - 两个模型卡片均显示「自动：65536」；
   - 模型 #1 填 16384 → 保存 → 磁盘 `vlm_models[0].max_tokens = 16384`、模型 #2 仍为 `null`，
     questions 24 维及其他字段无损；
   - 清空 → 保存 → 磁盘恢复 `null`；
   - 重启后端后自动值展示仍正常。

## 注意事项

- 自动查询依赖供应商的 `/models` 接口返回最大输出字段；OpenAI 官方接口的 `/models` 条目目前
  不带该字段，会走 8192 回退——如需更大预算请在模型条目里显式填数值。
- `max_tokens` 过大不会多花钱（按实际生成计费），但个别端点会对超出其实际上限的请求报错，
  遇到时可为该模型显式填一个较小的值。
- thinking 模型（sensenova、DeepSeek-R1、o 系列等）的思考与答案共用此预算，切勿为省 token 调小。
