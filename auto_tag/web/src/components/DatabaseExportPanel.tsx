import { useCallback, useState } from 'react'
import { api, type ExportOptions, type ExportSaveResult } from '../api/client'
import type { ExportDeliveryConfig } from './ExportDeliveryBox'

const EXPORT_MAX = 200_000

type ExportMode = 'metadata' | 'compact'

const inputCls =
  'border border-gray-300 dark:border-gray-600 rounded px-2 py-1 text-sm bg-white dark:bg-gray-900 dark:text-gray-200 w-full'

const MODE_INTRO: Record<ExportMode, string> = {
  metadata:
    '原始分片导出：向量索引与近重复侧车各存一份，合起来才覆盖流水线处理过的全部图片（Stage1 近重复只在侧车）。适合备份 Chroma、审计 metadata。',
  compact:
    '合并视图导出：由索引 + 侧车现场拼装，先下载共享字典，再下载数据切片（slice 或 chunk）。适合训练与下游消费。',
}

function NumInput({
  label,
  value,
  onChange,
  min = 0,
}: {
  label: string
  value: number
  onChange: (n: number) => void
  min?: number
}) {
  return (
    <label className="block text-xs text-gray-500 dark:text-gray-400">
      {label}
      <input
        type="number"
        min={min}
        value={value}
        onChange={e => onChange(Number(e.target.value))}
        className={`${inputCls} mt-0.5`}
      />
    </label>
  )
}

function ExportCard({
  title,
  subtitle,
  endpoint,
  children,
}: {
  title: string
  subtitle: string
  endpoint: string
  children: React.ReactNode
}) {
  return (
    <div className="flex flex-col rounded-lg border border-gray-200 bg-gray-50/60 p-4 dark:border-gray-600 dark:bg-gray-900/30">
      <h4 className="text-sm font-medium text-gray-800 dark:text-gray-100">{title}</h4>
      <p className="mt-1 text-xs leading-relaxed text-gray-500 dark:text-gray-400">{subtitle}</p>
      <code className="mt-2 block break-all text-[10px] text-gray-400 dark:text-gray-500">{endpoint}</code>
      <div className="mt-3 flex flex-1 flex-col gap-3">{children}</div>
    </div>
  )
}

function ActionButton({
  label,
  onClick,
  busy,
  variant = 'secondary',
}: {
  label: string
  onClick: () => void | Promise<void>
  busy?: boolean
  variant?: 'primary' | 'secondary'
}) {
  const cls =
    variant === 'primary'
      ? 'bg-blue-600 text-white hover:bg-blue-700'
      : 'border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700'
  return (
    <button
      type="button"
      onClick={() => void onClick()}
      disabled={busy}
      className={`w-full rounded px-3 py-2 text-sm font-medium disabled:opacity-50 ${cls}`}
    >
      {busy ? '导出中…' : label}
    </button>
  )
}

function ModeSwitch({
  mode,
  onChange,
}: {
  mode: ExportMode
  onChange: (m: ExportMode) => void
}) {
  const tabCls = (active: boolean) =>
    active
      ? 'bg-white text-gray-900 shadow-sm dark:bg-gray-700 dark:text-gray-100'
      : 'text-gray-600 hover:text-gray-900 dark:text-gray-400 dark:hover:text-gray-200'

  return (
    <div
      className="inline-flex rounded-lg border border-gray-200 bg-gray-100 p-1 dark:border-gray-600 dark:bg-gray-800"
      role="tablist"
      aria-label="导出模式"
    >
      <button
        type="button"
        role="tab"
        aria-selected={mode === 'metadata'}
        className={`rounded-md px-4 py-2 text-sm font-medium transition-colors ${tabCls(mode === 'metadata')}`}
        onClick={() => onChange('metadata')}
      >
        Metadata 原始分片
      </button>
      <button
        type="button"
        role="tab"
        aria-selected={mode === 'compact'}
        className={`rounded-md px-4 py-2 text-sm font-medium transition-colors ${tabCls(mode === 'compact')}`}
        onClick={() => onChange('compact')}
      >
        紧凑标注
      </button>
    </div>
  )
}

export default function DatabaseExportPanel({
  recordCount,
  dupCount,
  deliveryConfig,
  onMessage,
}: {
  recordCount: number
  dupCount: number
  deliveryConfig: ExportDeliveryConfig
  onMessage: (text: string) => void
}) {
  const [exportMode, setExportMode] = useState<ExportMode>('metadata')
  const [embRange, setEmbRange] = useState({ offset: 0, limit: EXPORT_MAX })
  const [embChunk, setEmbChunk] = useState({ chunk_index: 0, chunk_size: EXPORT_MAX })
  const [embClusterId, setEmbClusterId] = useState('')
  const [dupRange, setDupRange] = useState({ offset: 0, limit: EXPORT_MAX })
  const [dupChunk, setDupChunk] = useState({ chunk_index: 0, chunk_size: EXPORT_MAX })
  const [compactRange, setCompactRange] = useState({ offset: 0, limit: EXPORT_MAX })
  const [compactChunk, setCompactChunk] = useState({ chunk_index: 0, chunk_size: EXPORT_MAX })
  const [busyKey, setBusyKey] = useState<string | null>(null)

  const exportOptions = useCallback((): ExportOptions => {
    if (deliveryConfig.delivery === 'local') {
      return {
        delivery: 'local',
        outputDir: deliveryConfig.validatedPath || deliveryConfig.localDir,
      }
    }
    return { delivery: 'browser' }
  }, [deliveryConfig])

  const run = async (key: string, fn: () => Promise<ExportSaveResult | void>) => {
    if (deliveryConfig.delivery === 'local' && !deliveryConfig.validated) {
      onMessage('请先在本机目录模式下验证导出路径')
      return
    }
    setBusyKey(key)
    try {
      const res = await fn()
      if (deliveryConfig.delivery === 'local' && res && 'path' in res) {
        onMessage(`已保存到 ${res.path}（${res.bytes.toLocaleString()} 字节）`)
      } else {
        onMessage('导出已开始下载')
      }
    } catch (e: any) {
      onMessage(`导出失败: ${e.message}`)
    } finally {
      setBusyKey(null)
    }
  }

  const busy = (key: string) => busyKey === key

  return (
    <div className="space-y-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <ModeSwitch mode={exportMode} onChange={setExportMode} />
        <p className="text-xs text-gray-400 dark:text-gray-500">
          单次上限 {EXPORT_MAX.toLocaleString()} 条 · 超出请用 range 或 chunk 多次下载
        </p>
      </div>

      <p className="rounded-md border border-gray-200 bg-gray-50 px-3 py-2 text-xs leading-relaxed text-gray-600 dark:border-gray-600 dark:bg-gray-900/40 dark:text-gray-300">
        {MODE_INTRO[exportMode]}
      </p>

      {exportMode === 'metadata' && (
        <div
          className="grid gap-4 md:grid-cols-2"
          role="tabpanel"
          aria-label="Metadata 原始分片导出"
        >
          <ExportCard
            title="向量索引"
            subtitle="Chroma 每条记录的 id + metadata（cluster_id、labels_json、路径前缀等）。不含 Stage1 近重复且未入库的图。"
            endpoint="GET /api/database/export_embeddings"
          >
            <ActionButton
              label={`快速导出（前 ${Math.min(recordCount || EXPORT_MAX, EXPORT_MAX).toLocaleString()} 条）`}
              variant="primary"
              busy={busy('emb-quick')}
              onClick={() =>
                run('emb-quick', () =>
                  api.exportEmbeddings(
                    { mode: 'range', offset: 0, limit: EXPORT_MAX },
                    exportOptions(),
                  ),
                )
              }
            />
            <details className="text-xs">
              <summary className="cursor-pointer text-blue-600 dark:text-blue-400">
                自定义 range / cluster / chunk
              </summary>
              <div className="mt-2 space-y-2 rounded border border-gray-200 p-2 dark:border-gray-600">
                <p className="text-gray-500 dark:text-gray-400">按 offset + limit</p>
                <div className="grid grid-cols-2 gap-2">
                  <NumInput
                    label="offset"
                    value={embRange.offset}
                    onChange={v => setEmbRange(p => ({ ...p, offset: v }))}
                  />
                  <NumInput
                    label="limit"
                    value={embRange.limit}
                    onChange={v => setEmbRange(p => ({ ...p, limit: v }))}
                    min={1}
                  />
                </div>
                <ActionButton
                  label="导出 range"
                  busy={busy('emb-range')}
                  onClick={() =>
                    run('emb-range', () =>
                      api.exportEmbeddings(
                        {
                          mode: 'range',
                          offset: embRange.offset,
                          limit: embRange.limit,
                        },
                        exportOptions(),
                      ),
                    )
                  }
                />
                <p className="pt-1 text-gray-500 dark:text-gray-400">按 cluster_id</p>
                <input
                  type="text"
                  placeholder="cluster_id"
                  value={embClusterId}
                  onChange={e => setEmbClusterId(e.target.value)}
                  className={`${inputCls} font-mono`}
                />
                <ActionButton
                  label="导出该簇"
                  busy={busy('emb-cluster')}
                  onClick={() => {
                    if (!embClusterId.trim()) {
                      onMessage('请填写 cluster_id')
                      return
                    }
                    return run('emb-cluster', () =>
                      api.exportEmbeddings(
                        { mode: 'cluster', cluster_id: embClusterId.trim() },
                        exportOptions(),
                      ),
                    )
                  }}
                />
                <p className="pt-1 text-gray-500 dark:text-gray-400">
                  按 chunk（offset = chunk_index × chunk_size）
                </p>
                <div className="grid grid-cols-2 gap-2">
                  <NumInput
                    label="chunk_index"
                    value={embChunk.chunk_index}
                    onChange={v => setEmbChunk(p => ({ ...p, chunk_index: v }))}
                  />
                  <NumInput
                    label="chunk_size"
                    value={embChunk.chunk_size}
                    onChange={v => setEmbChunk(p => ({ ...p, chunk_size: v }))}
                    min={1}
                  />
                </div>
                <ActionButton
                  label="导出 chunk"
                  busy={busy('emb-chunk')}
                  onClick={() =>
                    run('emb-chunk', () =>
                      api.exportEmbeddings(
                        {
                          mode: 'chunk',
                          chunk_index: embChunk.chunk_index,
                          chunk_size: embChunk.chunk_size,
                        },
                        exportOptions(),
                      ),
                    )
                  }
                />
              </div>
            </details>
          </ExportCard>

          <ExportCard
            title="近重复侧车"
            subtitle="log 下 duplicate_links 中的 dup_path ↔ anchor_path。与向量索引互补，覆盖近重复未入库的图。"
            endpoint="GET /api/database/export_duplicates"
          >
            <ActionButton
              label={`快速导出（前 ${Math.min(dupCount || EXPORT_MAX, EXPORT_MAX).toLocaleString()} 条）`}
              variant="primary"
              busy={busy('dup-quick')}
              onClick={() =>
                run('dup-quick', () =>
                  api.exportDuplicates(
                    { mode: 'range', offset: 0, limit: EXPORT_MAX },
                    exportOptions(),
                  ),
                )
              }
            />
            <details className="text-xs">
              <summary className="cursor-pointer text-blue-600 dark:text-blue-400">自定义 range / chunk</summary>
              <div className="mt-2 space-y-2 rounded border border-gray-200 p-2 dark:border-gray-600">
                <div className="grid grid-cols-2 gap-2">
                  <NumInput
                    label="offset"
                    value={dupRange.offset}
                    onChange={v => setDupRange(p => ({ ...p, offset: v }))}
                  />
                  <NumInput
                    label="limit"
                    value={dupRange.limit}
                    onChange={v => setDupRange(p => ({ ...p, limit: v }))}
                    min={1}
                  />
                </div>
                <ActionButton
                  label="导出 range"
                  busy={busy('dup-range')}
                  onClick={() =>
                    run('dup-range', () =>
                      api.exportDuplicates(
                        {
                          mode: 'range',
                          offset: dupRange.offset,
                          limit: dupRange.limit,
                        },
                        exportOptions(),
                      ),
                    )
                  }
                />
                <div className="grid grid-cols-2 gap-2">
                  <NumInput
                    label="chunk_index"
                    value={dupChunk.chunk_index}
                    onChange={v => setDupChunk(p => ({ ...p, chunk_index: v }))}
                  />
                  <NumInput
                    label="chunk_size"
                    value={dupChunk.chunk_size}
                    onChange={v => setDupChunk(p => ({ ...p, chunk_size: v }))}
                    min={1}
                  />
                </div>
                <ActionButton
                  label="导出 chunk"
                  busy={busy('dup-chunk')}
                  onClick={() =>
                    run('dup-chunk', () =>
                      api.exportDuplicates(
                        {
                          mode: 'chunk',
                          chunk_index: dupChunk.chunk_index,
                          chunk_size: dupChunk.chunk_size,
                        },
                        exportOptions(),
                      ),
                    )
                  }
                />
              </div>
            </details>
          </ExportCard>
        </div>
      )}

      {exportMode === 'compact' && (
        <div
          className="grid gap-4 md:grid-cols-2"
          role="tabpanel"
          aria-label="紧凑标注导出"
        >
          <ExportCard
            title="共享字典"
            subtitle="labels / prefix / cluster / cluster_to_labels 字典。数据切片依赖本文件解析 id，请先下载（全库只需一份）。"
            endpoint="GET /api/database/export_compact_shared"
          >
            <ActionButton
              label="下载共享字典"
              variant="primary"
              busy={busy('compact-shared')}
              onClick={() => run('compact-shared', () => api.exportCompactShared({}, exportOptions()))}
            />
          </ExportCard>

          <ExportCard
            title="数据切片"
            subtitle="平行数组 images、labels_id、prefix_id、cluster_id。与共享字典合并后得到每张图的路径与有效标注。"
            endpoint="GET /api/database/export_compact_slice | export_compact_chunk"
          >
            <ActionButton
              label={`快速导出（offset=0, limit=${EXPORT_MAX.toLocaleString()}）`}
              variant="primary"
              busy={busy('compact-slice')}
              onClick={() =>
                run('compact-slice', () =>
                  api.exportCompactSlice({ offset: 0, limit: EXPORT_MAX }, exportOptions()),
                )
              }
            />
            <details className="text-xs">
              <summary className="cursor-pointer text-blue-600 dark:text-blue-400">自定义 slice / chunk</summary>
              <div className="mt-2 space-y-2 rounded border border-gray-200 p-2 dark:border-gray-600">
                <p className="text-gray-500 dark:text-gray-400">slice（offset + limit）</p>
                <div className="grid grid-cols-2 gap-2">
                  <NumInput
                    label="offset"
                    value={compactRange.offset}
                    onChange={v => setCompactRange(p => ({ ...p, offset: v }))}
                  />
                  <NumInput
                    label="limit"
                    value={compactRange.limit}
                    onChange={v => setCompactRange(p => ({ ...p, limit: v }))}
                    min={1}
                  />
                </div>
                <ActionButton
                  label="导出 slice"
                  busy={busy('compact-slice-custom')}
                  onClick={() =>
                    run('compact-slice-custom', () =>
                      api.exportCompactSlice(
                        {
                          offset: compactRange.offset,
                          limit: compactRange.limit,
                        },
                        exportOptions(),
                      ),
                    )
                  }
                />
                <p className="pt-1 text-gray-500 dark:text-gray-400">chunk（chunk_index × chunk_size）</p>
                <div className="grid grid-cols-2 gap-2">
                  <NumInput
                    label="chunk_index"
                    value={compactChunk.chunk_index}
                    onChange={v => setCompactChunk(p => ({ ...p, chunk_index: v }))}
                  />
                  <NumInput
                    label="chunk_size"
                    value={compactChunk.chunk_size}
                    onChange={v => setCompactChunk(p => ({ ...p, chunk_size: v }))}
                    min={1}
                  />
                </div>
                <ActionButton
                  label="导出 chunk"
                  busy={busy('compact-chunk')}
                  onClick={() =>
                    run('compact-chunk', () =>
                      api.exportCompactChunk(
                        {
                          chunk_index: compactChunk.chunk_index,
                          chunk_size: compactChunk.chunk_size,
                        },
                        exportOptions(),
                      ),
                    )
                  }
                />
              </div>
            </details>
          </ExportCard>
        </div>
      )}
    </div>
  )
}
