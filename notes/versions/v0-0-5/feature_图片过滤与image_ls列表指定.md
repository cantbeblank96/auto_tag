# v0.0.5 Feature：图片过滤与 image_ls 列表指定 —— 任务输入范围精确控制

> 背景：评测回归时经常需要对同一批数据做复测，但流水线只能「整目录扫描」，要么全量重跑
> （成本高），要么手工把目标图片拷到临时目录（易出错）。本版本新增两种可切换的输入方式：
> ①目录扫描 + 后缀/正则过滤；②image_ls 列表文件精确指定，降低复测与回归成本。

## 设计：双模式

前端「新建任务」区顶部提供两个切换按钮：

1. **目录扫描**（默认，原有行为）：输入一个或多个目录，可叠加过滤条件；
2. **列表指定 (image_ls)**：直接输入一个或多个 image_ls 文件的**本地绝对路径**（不上传），
   文件里列出要处理的图片。

两者互斥：目录模式不传 `image_ls_files`，列表模式不传 `input_dirs`。

### 过滤字段（仅目录扫描模式生效）

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `image_suffixes` | `List[str]` | `null`（不过滤） | 后缀过滤，自动补点/去重 |
| `image_name_regex` | `str` | `null` | 正则过滤；**非空时优先于后缀** |
| `filter_ignore_case` | `bool` | `true` | 是否忽略大小写 |
| `filter_match_full_path` | `bool` | `false` | 正则匹配完整路径（默认仅文件名） |

- 后缀模式：直接用过滤后缀扫描目录（因此可命中默认常见后缀之外的类型，如 `.tif`）；
- 正则模式：先按默认后缀扫描，再 `re.search`；默认对 `basename` 匹配，勾选后对完整路径匹配；
- 过滤**只作用于 input_dirs 扫描**；image_ls 是显式列表，不参与过滤。

## image_ls v2 格式

`_read_image_list` 支持两种格式（自动识别）：

**v2（新）**：首个非空行以 `{` 开头时为 JSON 头部，其余每行一个图片路径：

```
{"prefix": "/data/imgs/", "image_num": 3}
a.jpg
b.jpg
/data/other/c.jpg
```

- `prefix`：共有前缀，相对路径行自动拼接；绝对路径行原样使用；
- `image_num`（可选）：预期图片数量。与实际行数不一致时仅 **WARNING 日志，不中断任务**；
  无该字段则跳过核验；
- 无 `prefix` 时出现相对行：跳过该行并计数 WARNING；
- 头部 JSON 非法：抛 `ValueError`，API 层转 400。

**旧格式兼容**：整文件是 JSON 数组（以 `[` 开头且可解析）时按旧逻辑处理。

## 实现

- **`auto_tag/core/pipeline.py`**
  - `PipelineConfig` 新增 4 个过滤字段；
  - 新增 `normalize_image_suffixes(raw, lowercase)` / `ImageFilterSpec` /
    `build_image_filter_spec(...)`（正则非法、后缀全无效 → `ValueError`）/
    `_apply_image_filter(paths, spec)`；
  - `collect_image_paths(input_dirs, image_ls_files, filter_spec=None)` 接入过滤；
  - `_read_image_list` 实现 v2 解析（保留 `_read_image_list_json` 别名）。
- **`auto_tag/backend/routers/jobs.py`**：`JobCreate` 新增 4 字段并透传；`create_job` 提前
  校验——过滤条件非法 → 400，image_ls 文件不存在 → 400，头部解析失败 → 400。
- **`auto_tag/backend/job_runner.py`**：预扫描 `collect_image_paths` 同步带 `filter_spec`，
  保证任务 `total` 与实际执行一致。
- **`auto_tag/main.py`**：CLI 新增 `--image_suffix`（可重复）、`--image_name_regex`、
  `--filter_case_sensitive`、`--filter_match_full_path`；构建失败打印错误并 `exit(2)`。
- **`auto_tag/web/src/pages/Tasks.tsx` + `api/client.ts`**：双模式表单——目录模式展示过滤面板
  （按后缀/按正则 radio、后缀输入、正则输入、忽略大小写、匹配完整路径勾选）；列表模式展示
  image_ls 路径多行输入框与格式提示；任务 JSON 保存/加载携带全部新字段。

### 大小写细节（实现中修复过的坑）

`_walk_collect_images` 恒以「小写后缀」匹配。因此忽略大小写时后缀归一化为小写后在扫描阶段
直接完成过滤；**区分大小写**时保留用户原样后缀，由 `_apply_image_filter` 二次筛选
（如 `.jpg` + 区分大小写只命中 `b.jpg`，不命中 `a.JPG`）。

## 使用方式

**前端**：任务页「新建」区切换模式后填写，随任务提交；任务 JSON 导入/导出兼容。

**CLI**：

```bash
# 后缀过滤（可重复）
python -m auto_tag.main --input_dir /data/imgs --image_suffix .jpg --image_suffix .png

# 正则过滤（优先于后缀）
python -m auto_tag.main --input_dir /data/imgs --image_name_regex '.*_front\.jpg$' \
  --filter_case_sensitive --filter_match_full_path

# image_ls
python -m auto_tag.main --image_ls_file /data/list_a.txt --image_ls_file /data/list_b.txt
```

**API**：`POST /api/jobs` 携带 `image_suffixes` / `image_name_regex` /
`filter_ignore_case` / `filter_match_full_path` / `image_ls_files`。

## 验证记录

- 后端单测 14 项全过：无过滤 / 后缀过滤 / 忽略大小写 / 大写后缀 / 正则 / 匹配完整路径 /
  正则区分大小写 / 非法正则报错 / v2 头部前缀拼接 / image_num 不一致仅 WARNING /
  旧 JSON 数组兼容 / 头部非法报错 / 无 prefix 相对行跳过 / 列表不受过滤影响；
- API 400 校验 4 项：非法正则、image_ls 文件不存在、头部 JSON 非法、后缀全空；
- 前端 e2e（playwright）：双模式切换、过滤面板各控件、列表模式 textarea 与格式提示、
  JSON 保存/加载回显；
- 真实任务端到端：`test_data`（7 张图）+ `image_suffixes: [".png"]` → job `dd0a4efe`
  `status=done, total=1, processed=1, vlm_calls=1, failed=0`，证明过滤在
  API → job_runner 预扫描 → pipeline 执行全链路生效。

## 使用建议

- 回归复测优先用 **image_ls**：一次生成列表后可反复使用，数量核验（`image_num`）能及早
  发现列表文件损坏/截断；
- 大批量目录跑批用后缀过滤缩窄类型范围；正则适合按命名规则抽样（如 `front` 视角）；
- 过滤不影响库内去重逻辑（`skip_if_in_db` 语义不变），两者可叠加。
