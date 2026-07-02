import { useState } from 'react'

const sectionCls = "bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4 mb-6 max-w-3xl"
const codeCls = "bg-gray-100 dark:bg-gray-700 px-1 rounded text-xs font-mono"
const preCls = "bg-gray-50 dark:bg-gray-900 p-3 rounded border dark:border-gray-600 overflow-x-auto text-xs font-mono dark:text-gray-300"

export default function Tutorial() {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})

  const toggle = (key: string) => setExpanded(p => ({ ...p, [key]: !p[key] }))

  const Section = ({ id, title, children }: { id: string; title: string; children: React.ReactNode }) => (
    <section className={sectionCls}>
      <h3
        className="text-sm font-medium text-gray-700 dark:text-gray-300 cursor-pointer select-none flex items-center gap-2 mb-3"
        onClick={() => toggle(id)}
      >
        <span className="text-xs text-blue-500">{expanded[id] ? '▼' : '▶'}</span>
        {title}
      </h3>
      {expanded[id] && <div className="space-y-3 text-sm text-gray-600 dark:text-gray-400">{children}</div>}
    </section>
  )

  return (
    <div>
      <h2 className="text-2xl font-semibold text-gray-800 dark:text-gray-100 mb-2">使用教程</h2>
      <p className="text-xs text-gray-500 dark:text-gray-400 mb-6">点击各节标题展开阅读。</p>

      {/* ── Quick Start ── */}
      <section className={sectionCls}>
        <h3
          className="text-sm font-medium text-gray-700 dark:text-gray-300 cursor-pointer select-none flex items-center gap-2 mb-0"
          onClick={() => toggle('quickstart')}
        >
          <span className="text-xs text-blue-500">{expanded['quickstart'] ? '▼' : '▶'}</span>
          快速开始
        </h3>
        {expanded['quickstart'] && (
          <div className="mt-3 space-y-3 text-sm text-gray-600 dark:text-gray-400">
            <p>
              本系统是一个自动化图片标注流水线。它使用 <strong>CLIP</strong> 模型提取图像特征，
              通过双阈值策略进行去重与聚类，再用 <strong>VLM</strong>（视觉大语言模型）为每张图生成结构化标注。
            </p>
            <p>完整的使用流程如下：</p>
            <ol className="list-decimal list-inside space-y-1">
              <li>在「设置」中配置 VLM 模型（API Key、Base URL）</li>
              <li>在「标注任务」中填写输入目录，确认后提交任务</li>
              <li>等待流水线执行完成（可在页面实时查看进度）</li>
              <li>在「图片查询」中按路径检索标注结果</li>
              <li>在「数据库」中查看统计、对比参数、导出数据</li>
            </ol>
          </div>
        )}
      </section>

      {/* ── Work Directory ── */}
      <section className={sectionCls}>
        <h3
          className="text-sm font-medium text-gray-700 dark:text-gray-300 cursor-pointer select-none flex items-center gap-2 mb-0"
          onClick={() => toggle('workdir')}
        >
          <span className="text-xs text-blue-500">{expanded['workdir'] ? '▼' : '▶'}</span>
          工作目录（work_dir）结构
        </h3>
        {expanded['workdir'] && (
          <div className="mt-3 space-y-3 text-sm text-gray-600 dark:text-gray-400">
            <p>
              每个标注任务使用一个 <strong>work_dir</strong>（默认 <code className={codeCls}>{'{PROJECT_PATH}'}/work</code>）。
              任务执行后在该目录下自动创建以下子目录结构：
            </p>
            <pre className={preCls}>
{`work_dir/                  # 工作根目录
├── embedding_index/       # 向量索引（ChromaDB 持久化目录）
│   ├── chroma.sqlite3     # 索引元数据
│   └── ...                # 向量数据文件
├── log/                   # 运行日志与快照
│   ├── auto_tag.log       # 流水线运行日志
│   ├── auto_tag_db_build_snapshot.json  # 构建快照
│   ├── failed_images.json # 处理失败的图片列表
│   ├── verify_*.png       # 各输入源的首张样图
│   ├── duplicate_links.sqlite  # 近重复对侧车
│   └── path_prefix_registry.json   # 路径前缀注册表
└── chroma_data/           # （旧版，自动兼容）`}
            </pre>
            <p className="text-xs text-gray-400">
              <strong>注意</strong>：若目录下存在旧版 <code className={codeCls}>chroma_data</code> 且新目录尚未创建，
              系统会自动继续使用 <code className={codeCls}>chroma_data</code> 以避免破坏已有数据。
            </p>
          </div>
        )}
      </section>

      {/* ── Annotation Pipeline ── */}
      <section className={sectionCls}>
        <h3
          className="text-sm font-medium text-gray-700 dark:text-gray-300 cursor-pointer select-none flex items-center gap-2 mb-0"
          onClick={() => toggle('pipeline')}
        >
          <span className="text-xs text-blue-500">{expanded['pipeline'] ? '▼' : '▶'}</span>
          标注流水线详解
        </h3>
        {expanded['pipeline'] && (
          <div className="mt-3 space-y-3 text-sm text-gray-600 dark:text-gray-400">
            <p>流水线执行以下步骤：</p>
            <ol className="list-decimal list-inside space-y-2">
              <li>
                <strong>图片收集</strong> — 扫描输入目录下所有支持格式的图片文件
                （<code className={codeCls}>.jpg / .png / .bmp / .webp / .yuv / .nv21 / .nv12</code>）。
              </li>
              <li>
                <strong>样图校验</strong> — 从每个输入源选取首张样图保存到
                <code className={codeCls}> log/verify_*.png</code>，供人工确认。
              </li>
              <li>
                <strong>CLIP 特征提取</strong> — 使用 CLIP 模型批量提取图像特征向量并做 L2 归一化。
              </li>
              <li>
                <strong>双阈值聚类</strong> — 对每张图做最近邻查询：
                <ul className="list-disc list-inside ml-4 mt-1 space-y-1">
                  <li><strong>Stage 1（τ_dup）</strong>：距离 ≤ τ_dup → 判定为近重复帧→ 跳过入库，记录到侧车</li>
                  <li><strong>Stage 2（τ_cls）</strong>：τ_dup &lt; 距离 ≤ τ_cls → 并入已有簇，继承簇标签</li>
                  <li><strong>Stage 3</strong>：距离 &gt; τ_cls → 创建新簇 → 调用 VLM 生成标注</li>
                </ul>
              </li>
              <li>
                <strong>VLM 标注</strong> — 为新簇的中心图调用 VLM 接口，按「设置」中的
                questions 定义生成结构化 JSON 标签。
              </li>
              <li>
                <strong>写入索引</strong> — 所有结果写入 ChromaDB 向量索引。
              </li>
            </ol>
          </div>
        )}
      </section>

      {/* ── VLM Models ── */}
      <section className={sectionCls}>
        <h3
          className="text-sm font-medium text-gray-700 dark:text-gray-300 cursor-pointer select-none flex items-center gap-2 mb-0"
          onClick={() => toggle('vlm')}
        >
          <span className="text-xs text-blue-500">{expanded['vlm'] ? '▼' : '▶'}</span>
          VLM 模型配置
        </h3>
        {expanded['vlm'] && (
          <div className="mt-3 space-y-3 text-sm text-gray-600 dark:text-gray-400">
            <p>
              系统支持多个 VLM 模型，可通过两种策略调用：
            </p>
            <ul className="list-disc list-inside space-y-1">
              <li>
                <strong>优先级顺序（Failover）</strong>：按优先级依次尝试，第一个成功的返回；
                若失败则自动切换到下一个。适用于主备模型场景。
              </li>
              <li>
                <strong>均衡负载（Round-Robin）</strong>：轮询可用模型。适用于多个等价模型分摊请求量。
              </li>
            </ul>
            <p>熔断器会在模型连续失败时自动暂时停用（可在「设置」中调整参数）。</p>
            <p>支持的模型配置项：</p>
            <ul className="list-disc list-inside space-y-1">
              <li><strong>模型名称</strong> — 传递给 API 的 model 参数（如 <code className={codeCls}>gemini/gemini-2.5-flash</code>）</li>
              <li><strong>Base URL</strong> — OpenAI 兼容的 API 地址（可选；默认使用 OpenAI 官方地址）</li>
              <li><strong>API Key</strong> — 认证密钥</li>
            </ul>
          </div>
        )}
      </section>

      {/* ── Image Query ── */}
      <section className={sectionCls}>
        <h3
          className="text-sm font-medium text-gray-700 dark:text-gray-300 cursor-pointer select-none flex items-center gap-2 mb-0"
          onClick={() => toggle('query')}
        >
          <span className="text-xs text-blue-500">{expanded['query'] ? '▼' : '▶'}</span>
          图片查询与标注编辑
        </h3>
        {expanded['query'] && (
          <div className="mt-3 space-y-3 text-sm text-gray-600 dark:text-gray-400">
            <p>在「图片查询」页面可按图片绝对路径检索：</p>
            <ol className="list-decimal list-inside space-y-2">
              <li>
                <strong>填写路径</strong> — 输入图片在磁盘上的绝对路径，选择对应的 work_dir。
              </li>
              <li>
                <strong>查询</strong> — 系统先在向量索引中查找，若未找到再到侧车中查近重复记录。
                <ul className="list-disc list-inside ml-4 mt-1">
                  <li>索引命中：显示簇信息、中心图标签、有效标签</li>
                  <li>侧车命中：显示近重复对列表，自动查询锚点在索引中的记录</li>
                </ul>
              </li>
              <li>
                <strong>编辑标签</strong> — 直接修改 JSON，可选择仅更新该图（<code className={codeCls}>image_only</code>）
                或同步整簇（<code className={codeCls}>with_cluster</code>）。
              </li>
              <li>
                <strong>预览</strong> — 加载图片预览（支持 YUV 格式自动解码）。
              </li>
            </ol>
          </div>
        )}
      </section>

      {/* ── Database ── */}
      <section className={sectionCls}>
        <h3
          className="text-sm font-medium text-gray-700 dark:text-gray-300 cursor-pointer select-none flex items-center gap-2 mb-0"
          onClick={() => toggle('database_help')}
        >
          <span className="text-xs text-blue-500">{expanded['database_help'] ? '▼' : '▶'}</span>
          数据库与导出
        </h3>
        {expanded['database_help'] && (
          <div className="mt-3 space-y-3 text-sm text-gray-600 dark:text-gray-400">
            <p>「数据库」页面提供索引概览与参数对比：</p>
            <ul className="list-disc list-inside space-y-1">
              <li><strong>统计卡片</strong> — 展示索引条数、簇数量、已标注数和近重复对条数。</li>
              <li>
                <strong>参数对比</strong> — 将「当前 config 参数」与「上次成功任务写入的快照」逐项比较，
                不一致的参数会高亮显示。
              </li>
              <li>
                <strong>更新标注</strong> — 按当前 questions 重跑 VLM 标注（全量或增量）。
              </li>
              <li>
                <strong>导出</strong> — 支持三种导出格式：
                <ul className="list-disc list-inside ml-4">
                  <li>索引记录（按 offset/limit、cluster、或分块）</li>
                  <li>近重复侧车记录</li>
                  <li>紧凑标注导出（共享字典 + 平行数组）</li>
                </ul>
              </li>
            </ul>
          </div>
        )}
      </section>

      {/* ── YUV Support ── */}
      <section className={sectionCls}>
        <h3
          className="text-sm font-medium text-gray-700 dark:text-gray-300 cursor-pointer select-none flex items-center gap-2 mb-0"
          onClick={() => toggle('yuv')}
        >
          <span className="text-xs text-blue-500">{expanded['yuv'] ? '▼' : '▶'}</span>
          YUV 图片支持
        </h3>
        {expanded['yuv'] && (
          <div className="mt-3 space-y-3 text-sm text-gray-600 dark:text-gray-400">
            <p>系统支持处理原始 YUV 格式图片（常用于车载摄像头等视觉领域）：</p>
            <ul className="list-disc list-inside space-y-1">
              <li>支持格式：<code className={codeCls}>.nv21 / .nv12 / .yuv</code></li>
              <li>两种模式：
                <ul className="list-disc list-inside ml-4">
                  <li><strong>整批 YUV</strong> — 所有输入文件都按 YUV 解码</li>
                  <li><strong>混合目录</strong> — 同目录下 JPG/PNG 按图处理，<code className={codeCls}>.nv21/.nv12/.yuv</code> 按 YUV 处理</li>
                </ul>
              </li>
              <li>解码参数（宽、高、类型）在提交任务时指定，且会随向量元数据保存，
                后续在图片查询页预览时会自动沿用。</li>
            </ul>
          </div>
        )}
      </section>

      {/* ── CLI Usage ── */}
      <section className={sectionCls}>
        <h3
          className="text-sm font-medium text-gray-700 dark:text-gray-300 cursor-pointer select-none flex items-center gap-2 mb-0"
          onClick={() => toggle('cli')}
        >
          <span className="text-xs text-blue-500">{expanded['cli'] ? '▼' : '▶'}</span>
          命令行使用
        </h3>
        {expanded['cli'] && (
          <div className="mt-3 space-y-3 text-sm text-gray-600 dark:text-gray-400">
            <p>除 Web 界面外，流水线也可以通过命令行直接运行：</p>
            <pre className={preCls}>
{`# 激活环境
conda activate agent_d
export PYTHONPATH=$PYTHONPATH:.

# 运行流水线
python -m auto_tag.main \\
    --input_dir /path/to/images \\
    --work_dir ./work

# 可选：自定义配置文件
python -m auto_tag.main \\
    --input_dir /path/to/images \\
    --work_dir ./work \\
    --config_file /path/to/config.json

# 查看索引中的标注结果
python -m auto_tag.view_db
python -m auto_tag.view_db --output_path output.json`}
            </pre>
          </div>
        )}
      </section>

      {/* ── Questions Configuration ── */}
      <section className={sectionCls}>
        <h3
          className="text-sm font-medium text-gray-700 dark:text-gray-300 cursor-pointer select-none flex items-center gap-2 mb-0"
          onClick={() => toggle('questions')}
        >
          <span className="text-xs text-blue-500">{expanded['questions'] ? '▼' : '▶'}</span>
          配置 VLM 的 Qeustions
        </h3>
        {expanded['questions'] && (
          <div className="mt-3 space-y-3 text-sm text-gray-600 dark:text-gray-400">
            <p>
              Questions 定义了 VLM 为每张图生成的 JSON 标注结构。每个 question 是一个字段，
              包含类型约束和描述。
            </p>
            <p>支持的类型：</p>
            <ul className="list-disc list-inside space-y-1">
              <li><strong>string</strong> — 自由文本描述</li>
              <li><strong>category</strong> — 分类选择（从指定的 choices 中选取）</li>
              <li><strong>int</strong> — 整数（可设置 min/max）</li>
              <li><strong>float</strong> — 浮点数（可设置 min/max/step）</li>
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
    "step": 0.1
  }
}`}
            </pre>
          </div>
        )}
      </section>

      <p className="text-xs text-gray-400 text-center py-4">
        v0.1.0 · 如有问题请联系 xukaiming1996@163.com
      </p>
    </div>
  )
}