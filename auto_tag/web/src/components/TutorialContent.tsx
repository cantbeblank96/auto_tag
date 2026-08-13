import { useState } from 'react'

const sectionCls =
  'bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4 mb-4 max-w-3xl'
const codeCls = 'bg-gray-100 dark:bg-gray-700 px-1 rounded text-xs font-mono'
const preCls =
  'bg-gray-50 dark:bg-gray-900 p-3 rounded border dark:border-gray-600 overflow-x-auto text-xs font-mono dark:text-gray-300'

/** 可折叠的使用教程正文（用于首页等）。 */
export default function TutorialContent() {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})

  const toggle = (key: string) => setExpanded((p) => ({ ...p, [key]: !p[key] }))

  const SectionHeader = ({ id, title }: { id: string; title: string }) => (
    <h4
      className="text-sm font-medium text-gray-700 dark:text-gray-300 cursor-pointer select-none flex items-center gap-2"
      onClick={() => toggle(id)}
    >
      <span className="text-xs text-blue-500">{expanded[id] ? '▼' : '▶'}</span>
      {title}
    </h4>
  )

  return (
    <div>
      <p className="text-xs text-gray-500 dark:text-gray-400 mb-4">点击各节标题展开阅读。</p>

      <section className={sectionCls}>
        <SectionHeader id="nav" title="控制台导航" />
        {expanded.nav && (
          <div className="mt-3 space-y-3 text-sm text-gray-600 dark:text-gray-400">
            <p>
              开发环境默认地址：<strong>http://localhost:5020</strong>（Vite 将{' '}
              <code className={codeCls}>/api</code> 代理到后端 <strong>8000</strong>）。
            </p>
            <ul className="list-disc list-inside space-y-1">
              <li>
                <strong>Auto Tag（首页）</strong> — 本页：使用教程、健康检查、重启后端、版本信息
              </li>
              <li>
                <strong>任务</strong> — 新建/确认/提交标注任务，监控进度；页底「查询」可浏览全部历史（默认折叠）
              </li>
              <li>
                <strong>数据库</strong> — 索引统计、快照与当前 config 比对、维护（重算/重建/重标注）、导出
              </li>
              <li>
                <strong>图片查询</strong> — 按绝对路径检索标注、预览、编辑标签
              </li>
              <li>
                <strong>设置</strong> — VLM 模型、熔断器、Questions、<code className={codeCls}>config.json</code>{' '}
                路径
              </li>
            </ul>
          </div>
        )}
      </section>

      <section className={sectionCls}>
        <SectionHeader id="quickstart" title="快速开始" />
        {expanded.quickstart && (
          <div className="mt-3 space-y-3 text-sm text-gray-600 dark:text-gray-400">
            <p>
              本系统是一个自动化图片标注流水线。它使用 <strong>CLIP</strong> 模型提取图像特征，
              通过双阈值策略进行去重与聚类，再用 <strong>VLM</strong>（视觉大语言模型）为每张图生成结构化标注。
            </p>
            <p>推荐首次使用流程：</p>
            <ol className="list-decimal list-inside space-y-1">
              <li>
                在仓库根目录执行 <code className={codeCls}>bash scripts/linux/setup_uv_env.sh</code>{' '}
                创建 <code className={codeCls}>.venv</code> 并安装依赖（Windows 见{' '}
                <code className={codeCls}>scripts/windows/</code>）
              </li>
              <li>启动后端与前端脚本，浏览器打开控制台</li>
              <li>在「设置」中配置 VLM 模型（API Key、Base URL），可点「测试连接」</li>
              <li>在「任务」中填写输入目录（或 image_ls 列表），按需设置过滤条件，确认后提交任务</li>
              <li>等待流水线完成（约 2 秒轮询；运行中任务显示<strong>建簇</strong>与<strong>VLM 标注</strong>两条进度）</li>
              <li>在「图片查询」中按路径检索标注；在「数据库」中查看统计、比对参数、导出数据</li>
            </ol>
            <p className="text-xs text-gray-400">
              修改「设置」并保存后，可在本页「系统与其他 → 重启后端」使运行中的 FastAPI 重新读取磁盘{' '}
              <code className={codeCls}>config.json</code>（会中断进行中的任务）。
            </p>
          </div>
        )}
      </section>

      <section className={sectionCls}>
        <SectionHeader id="workdir" title="工作目录（work_dir）结构" />
        {expanded.workdir && (
          <div className="mt-3 space-y-3 text-sm text-gray-600 dark:text-gray-400">
            <p>
              每个标注任务使用一个 <strong>work_dir</strong>（由{' '}
              <code className={codeCls}>config.json</code> 的 <code className={codeCls}>work_dir</code>{' '}
              或任务参数指定）。任务执行后在该目录下自动创建以下子目录结构：
            </p>
            <pre className={preCls}>
              {`work_dir/                  # 工作根目录
├── embedding_index/       # 向量索引（ChromaDB 持久化目录，名见 embedding_subdir）
│   ├── chroma.sqlite3     # 索引元数据
│   └── ...                # 向量数据文件
├── log/                   # 运行日志与快照
│   ├── auto_tag.log       # 流水线运行日志
│   ├── auto_tag_db_build_snapshot.json  # 构建快照（数据库页比对用）
│   ├── failed_images.json # 处理失败的图片列表
│   ├── verify_*.png       # 各输入源的首张样图
│   ├── duplicate_links.sqlite  # 近重复对侧车
│   └── path_prefix_registry.json   # 路径前缀注册表
└── chroma_data/           # （旧版目录名，自动兼容）`}
            </pre>
            <p className="text-xs text-gray-400">
              <strong>注意</strong>：若目录下存在旧版 <code className={codeCls}>chroma_data</code>{' '}
              且新目录尚未创建，系统会自动继续使用 <code className={codeCls}>chroma_data</code>{' '}
              以避免破坏已有数据。
            </p>
          </div>
        )}
      </section>

      <section className={sectionCls}>
        <SectionHeader id="pipeline" title="标注流水线详解" />
        {expanded.pipeline && (
          <div className="mt-3 space-y-3 text-sm text-gray-600 dark:text-gray-400">
            <p>流水线执行以下步骤：</p>
            <ol className="list-decimal list-inside space-y-2">
              <li>
                <strong>图片收集</strong> — 支持两种来源：扫描输入目录（默认全部常见格式：
                <code className={codeCls}>.jpg / .png / .bmp / .webp / .yuv / .nv21 / .nv12</code>），或按{' '}
                <code className={codeCls}>image_ls</code> 列表文件显式指定图片集（见「任务页与历史查询」）。
                目录扫描可叠加过滤：按后缀白名单，或按文件名/完整路径正则（正则优先；可选忽略大小写）。
              </li>
              <li>
                <strong>样图校验</strong> — 从每个输入源选取首张样图保存到
                <code className={codeCls}> log/verify_*.png</code>，供人工确认（CLI 可{' '}
                <code className={codeCls}>--b_skip_image_manually_verified</code> 跳过）。
              </li>
              <li>
                <strong>CLIP 特征提取</strong> — 使用 CLIP 模型批量提取图像特征向量并做 L2 归一化。
              </li>
              <li>
                <strong>双阈值聚类</strong> — 对每张图做最近邻查询：
                <ul className="list-disc list-inside ml-4 mt-1 space-y-1">
                  <li>
                    <strong>Stage 1（τ_dup）</strong>：距离 ≤ τ_dup → 判定为近重复帧 → 跳过入库，记录到侧车
                  </li>
                  <li>
                    <strong>Stage 2（τ_cls）</strong>：τ_dup &lt; 距离 ≤ τ_cls → 并入已有簇，继承簇标签
                  </li>
                  <li>
                    <strong>Stage 3</strong>：距离 &gt; τ_cls → 创建新簇 → 调用 VLM 生成标注
                  </li>
                </ul>
              </li>
              <li>
                <strong>VLM 标注</strong> — 为新簇的中心图调用 VLM 接口，按「设置」中的 questions 定义生成结构化
                JSON 标签。
              </li>
              <li>
                <strong>写入索引</strong> — 结果写入 ChromaDB；成功结束后写入构建快照供数据库页比对。
              </li>
            </ol>
            <p className="text-xs text-gray-400">
              任务 API 返回 <code className={codeCls}>processed/total</code>（建簇）、
              <code className={codeCls}>vlm_calls/new_centers</code>（VLM）、
              <code className={codeCls}>skip_in_db</code>、<code className={codeCls}>stage1_skips</code>、
              <code className={codeCls}>stage2_joins</code>{' '}
              等字段，便于在「任务」页查看各阶段占比。
            </p>
          </div>
        )}
      </section>

      <section className={sectionCls}>
        <SectionHeader id="vlm" title="VLM 模型配置" />
        {expanded.vlm && (
          <div className="mt-3 space-y-3 text-sm text-gray-600 dark:text-gray-400">
            <p>系统支持多个 VLM 模型，可通过两种策略调用：</p>
            <ul className="list-disc list-inside space-y-1">
              <li>
                <strong>优先级顺序（Failover）</strong>：按优先级依次尝试，第一个成功的返回；若失败则自动切换到下一个。
              </li>
              <li>
                <strong>均衡负载（Round-Robin）</strong>：轮询可用模型，适用于多个等价模型分摊请求量。
              </li>
            </ul>
            <p>
              <strong>端点 id</strong>：每条 <code className={codeCls}>vlm_models</code> 配置应有唯一字段{' '}
              <code className={codeCls}>id</code>（UUID）。熔断器、连通性测试、重置熔断均按{' '}
              <strong>endpoint_id</strong> 区分——多条配置即使 <code className={codeCls}>name</code>{' '}
              相同（例如两个 SenseNova 账号）也不会共用熔断状态。保存设置时可为缺省条目自动生成 id。
            </p>
            <p>支持的模型配置项：</p>
            <ul className="list-disc list-inside space-y-1">
              <li>
                <strong>id</strong> — 端点唯一标识（推荐 UUID）
              </li>
              <li>
                <strong>模型名称（name）</strong> — 传递给 API 的 model 参数（如{' '}
                <code className={codeCls}>gemini/gemini-2.5-flash</code>）
              </li>
              <li>
                <strong>Base URL</strong> — OpenAI 兼容的 API 地址（可选）
              </li>
              <li>
                <strong>API Key</strong> — 认证密钥
              </li>
              <li>
                <strong>priority / enabled</strong> — Failover 顺序与是否启用
              </li>
            </ul>
            <p>熔断器会在端点连续失败时自动暂时停用（可在「设置」中调整时间窗口与失败率阈值）。</p>
          </div>
        )}
      </section>

      <section className={sectionCls}>
        <SectionHeader id="tasks_help" title="任务页与历史查询" />
        {expanded.tasks_help && (
          <div className="mt-3 space-y-3 text-sm text-gray-600 dark:text-gray-400">
            <p>「任务」页「新建」支持两种图片来源（可切换）：</p>
            <ul className="list-disc list-inside space-y-1">
              <li>
                <strong>目录扫描</strong> — 每行一个输入目录（绝对路径），可叠加图片过滤：
                <ul className="list-disc list-inside ml-4 mt-1">
                  <li>
                    <strong>按后缀</strong> — 逗号/换行分隔的后缀白名单（如 <code className={codeCls}>.jpg, .png</code>）；留空 = 全部常见后缀
                  </li>
                  <li>
                    <strong>按正则</strong> — 文件名正则（如 <code className={codeCls}>.*_front\.jpg$</code>），非空时优先于后缀过滤；
                    可选忽略大小写（默认）与匹配完整路径（默认仅文件名）
                  </li>
                </ul>
              </li>
              <li>
                <strong>列表指定（image_ls）</strong> — 每行一个列表文件路径，显式指定待处理图片集合，支持两种格式：
                <ul className="list-disc list-inside ml-4 mt-1">
                  <li>
                    <strong>v2 行式（推荐）</strong> — 首个非空行为 JSON 头部（含 <code className={codeCls}>prefix</code> /{' '}
                    <code className={codeCls}>image_num</code>），其余每行一个路径（UTF-8，空行忽略）；相对路径自动拼接{' '}
                    <code className={codeCls}>prefix</code>，无 prefix 的相对行跳过并告警；
                    <code className={codeCls}>image_num</code> 与实际行数不一致时仅警告不中断
                  </li>
                  <li><strong>JSON 数组</strong> — 文件整体为路径数组</li>
                </ul>
              </li>
            </ul>
            <p className="text-xs text-gray-400">
              注意：后缀/正则过滤仅作用于目录扫描；image_ls 显式列表不参与过滤。提交前会校验目录存在性与 image_ls 文件可读性，
              缺失目录弹窗确认后可选择仍要提交（会被流水线忽略）。
            </p>
            <p>「查询」章节列出服务端全部历史任务（默认折叠）：</p>
            <ul className="list-disc list-inside space-y-1">
              <li>支持按创建时间正序/倒序、手动刷新</li>
              <li>点击任务可查看 <code className={codeCls}>processed / total</code>、失败数、VLM 调用次数等</li>
              <li>
                <strong>清除历史显示</strong> 仅隐藏当前时刻之前的记录在界面中的展示，服务端仍保留数据；隐藏的记录仍可在「查询」中看到
              </li>
            </ul>
            <p>
              可通过「上传 JSON」加载预置任务（如测试仓库中的{' '}
              <code className={codeCls}>auto_tag/test/auto_tag_job.json</code>），确认目录存在后再入队提交。
            </p>
          </div>
        )}
      </section>

      <section className={sectionCls}>
        <SectionHeader id="query" title="图片查询与标注编辑" />
        {expanded.query && (
          <div className="mt-3 space-y-3 text-sm text-gray-600 dark:text-gray-400">
            <p>在「图片查询」页面可按图片绝对路径检索：</p>
            <ol className="list-decimal list-inside space-y-2">
              <li>
                <strong>填写路径</strong> — 输入图片在磁盘上的绝对路径（work_dir 由后端 config 统一管理时可留空）。
              </li>
              <li>
                <strong>查询</strong> — 系统先在向量索引中查找，若未找到再到侧车中查近重复记录。
                <ul className="list-disc list-inside ml-4 mt-1">
                  <li>索引命中：显示簇信息、中心图标签、有效标签</li>
                  <li>侧车命中：显示近重复对列表，并附带各锚点的 <code className={codeCls}>anchor_embedding_records</code></li>
                </ul>
              </li>
              <li>
                <strong>编辑标签</strong> — 直接修改 JSON，可选择仅更新该图（
                <code className={codeCls}>image_only</code>）或同步整簇（
                <code className={codeCls}>with_cluster</code>）。索引中无记录时可插入新条目。
              </li>
              <li>
                <strong>预览</strong> — 加载图片预览（支持 YUV 格式，沿用元数据中的宽高与 layout）。
              </li>
            </ol>
          </div>
        )}
      </section>

      <section className={sectionCls}>
        <SectionHeader id="database_help" title="数据库与导出" />
        {expanded.database_help && (
          <div className="mt-3 space-y-3 text-sm text-gray-600 dark:text-gray-400">
            <p>「数据库」页面提供索引概览与参数对比：</p>
            <ul className="list-disc list-inside space-y-1">
              <li>
                <strong>统计卡片</strong> — 展示索引条数、簇数量、已标注数和近重复对条数。
              </li>
              <li>
                <strong>参数对比</strong> — 将「当前 config」与「上次成功任务写入的快照」逐项比较；可传{' '}
                <code className={codeCls}>config_path</code> 与设置页路径一致，在未重启后端时比对磁盘 JSON。
              </li>
              <li>
                <strong>更新</strong> — 重算关系 / 完全重建 / 按 questions 重跑 VLM（全量或增量）；维护任务与标注任务互斥。JSON 详情区默认折叠。
              </li>
              <li>
                <strong>导出</strong> — 索引记录、侧车、紧凑标注（共享字典 + 分片/分块）；支持浏览器下载或写入本机目录（需后端写权限）。
              </li>
            </ul>
          </div>
        )}
      </section>

      <section className={sectionCls}>
        <SectionHeader id="yuv" title="YUV 图片支持" />
        {expanded.yuv && (
          <div className="mt-3 space-y-3 text-sm text-gray-600 dark:text-gray-400">
            <p>系统支持处理原始 YUV 格式图片（常用于车载摄像头等视觉领域）：</p>
            <ul className="list-disc list-inside space-y-1">
              <li>
                支持格式：<code className={codeCls}>.nv21 / .nv12 / .yuv</code>
              </li>
              <li>
                两种模式：
                <ul className="list-disc list-inside ml-4">
                  <li>
                    <strong>整批 YUV</strong> — 所有输入文件都按 YUV 解码
                  </li>
                  <li>
                    <strong>混合目录（mixed_yuv）</strong> — 同目录下 JPG/PNG 按图处理，
                    <code className={codeCls}>.nv21/.nv12/.yuv</code> 按 YUV 处理
                  </li>
                </ul>
              </li>
              <li>
                解码参数（宽、高、类型）在提交任务时指定，且会随向量元数据保存，后续在图片查询页预览时会自动沿用。
              </li>
            </ul>
          </div>
        )}
      </section>

      <section className={sectionCls}>
        <SectionHeader id="cli" title="命令行使用" />
        {expanded.cli && (
          <div className="mt-3 space-y-3 text-sm text-gray-600 dark:text-gray-400">
            <p>除 Web 界面外，流水线也可以通过命令行直接运行（在仓库根目录）：</p>
            <pre className={preCls}>
              {`# 首次：uv 创建 .venv 并安装依赖
bash scripts/linux/setup_uv_env.sh
source .venv/bin/activate
export PYTHONPATH=$PYTHONPATH:.

# 运行流水线
python -m auto_tag.main \\
    --input_dir /path/to/images \\
    --work_dir ./work

# 或：image_ls 列表指定 + 过滤（与 Web 任务页同能力）
python -m auto_tag.main \\
    --image_ls_file /path/to/image_ls.txt \\
    --image_suffix .jpg --image_name_regex '.*_front\\.jpg$' \\
    --work_dir ./work

# 可选：自定义配置文件
python -m auto_tag.main \\
    --input_dir /path/to/images \\
    --work_dir ./work \\
    --config_file /path/to/config.json

# 查看索引中的标注结果
python -m auto_tag.view_db
python -m auto_tag.view_db --output_path output.json

# 启动 Web（另开终端）
bash scripts/linux/run_web_backend.sh
bash scripts/linux/run_web_frontend_v2.sh`}
            </pre>
            <p className="text-xs text-gray-400">Windows 将 <code className={codeCls}>bash scripts/linux/...</code> 换为 <code className={codeCls}>scripts/windows/*.ps1</code> 对应脚本。</p>
          </div>
        )}
      </section>

      <section className={sectionCls}>
        <SectionHeader id="questions" title="配置 VLM 的 Questions" />
        {expanded.questions && (
          <div className="mt-3 space-y-3 text-sm text-gray-600 dark:text-gray-400">
            <p>
              Questions 定义了 VLM 为每张图生成的 JSON 标注结构。每个 question 是一个字段，包含类型约束和描述。
            </p>
            <p>支持的类型：</p>
            <ul className="list-disc list-inside space-y-1">
              <li>
                <strong>string</strong> — 自由文本描述
              </li>
              <li>
                <strong>category</strong> — 分类选择（从指定的 choices 中选取）
              </li>
              <li>
                <strong>int</strong> — 整数（可设置 min/max）
              </li>
              <li>
                <strong>float</strong> — 浮点数（可设置 min/max/step）
              </li>
            </ul>
            <p>示例配置：</p>
            <pre className={preCls}>
              {`{
  "scene": {
    "description": "description of the overall scene",
    "type": "string"
  },
  "time_of_day": {
    "description": "Determine the shooting time",
    "type": "category",
    "choices": ["day", "night", "unknown"]
  },
  "num_of_person": {
    "description": "Number of people",
    "type": "int",
    "min": 0
  },
  "brightness": {
    "description": "Brightness (0 to 10)",
    "type": "float",
    "min": 0,
    "max": 10,
    "step": 0.1,
    "examples": {
      "2.5": "examples/brightness_2.5.jpg",
      "7.5": "/abs/path/to/brightness_7.5.jpg"
    }
  }
}`}
            </pre>
            <p>
              <strong>参考样图（examples）</strong>：可为任意问题配置「档位值 → 样图路径」映射（如上例），
              标注时样图随 prompt 注入 VLM，帮助模型对齐评分尺度，尤其适合亮度/清晰度/人脸大小等主观连续量。
              在「设置 → Questions」卡片内可直接增删样图行；路径支持绝对路径或相对{' '}
              <code className={codeCls}>config.json</code> 目录的相对路径，保存后免重启生效。
            </p>
            <ul className="list-disc list-inside space-y-1 text-xs text-gray-400">
              <li>
                样图送入 VLM 前缩放到最长边 ≤ <code className={codeCls}>vlm_example_image_max_side</code>（默认 512，可在「设置」调整）
              </li>
              <li>样图加载失败仅告警跳过，不影响标注</li>
              <li>
                实验性增强：开启 <code className={codeCls}>annotation_tools_on_examples</code> 后，样图也会执行其维度绑定的标注工具，
                测量结果随样图注入供对比校准（见下节）
              </li>
            </ul>
          </div>
        )}
      </section>

      <section className={sectionCls}>
        <SectionHeader id="tools" title="标注工具（专项模型辅助）" />
        {expanded.tools && (
          <div className="mt-3 space-y-3 text-sm text-gray-600 dark:text-gray-400">
            <p>
              除纯 VLM 判断外，系统支持在标注时调用<strong>专项模型工具</strong>对待标注图（可选含参考样图）做客观测量，
              结果以文本/图片形式注入 VLM 辅助回答，最终标签仍由 VLM 综合判断，工具不硬覆盖。当前内置四个工具（基于可选库{' '}
              <code className={codeCls}>kevin_sdk</code>）：
            </p>
            <ul className="list-disc list-inside space-y-1">
              <li><strong>face_detect</strong> — 人脸数量、主脸位置框与占比</li>
              <li><strong>head_pose</strong> — 头部姿态角 yaw/pitch/roll</li>
              <li><strong>face_attribute</strong> — 性别、年龄、眼镜等属性分类</li>
              <li><strong>face_crop</strong> — 前 N 大人脸的转正裁剪图（以图片注入）</li>
            </ul>
            <p>使用方式（双层开关，两者都命中才执行）：</p>
            <ul className="list-disc list-inside space-y-1">
              <li>
                <strong>全局启用</strong> — 「设置 → 标注工具」中启用工具并配置模型路径；缺库/缺模型时自动标记不可用并静默跳过，不影响主流程
              </li>
              <li>
                <strong>问题级绑定</strong> — Questions 卡片内勾选「标注工具」（如 face_size 绑 face_detect），也可点「智能分析工具」由 VLM 建议绑定
              </li>
            </ul>
            <p className="text-xs text-gray-400">
              kevin_sdk 为作者私有库，未随仓库提供；缺失时人脸相关维度准确率可能略有下降，其余功能不受影响（详见 README）。
            </p>
          </div>
        )}
      </section>
    </div>
  )
}
