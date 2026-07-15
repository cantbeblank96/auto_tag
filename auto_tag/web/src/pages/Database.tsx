import { useState, useEffect, useCallback, useRef, type ReactNode } from 'react'
import { useLocation } from 'react-router-dom'
import { api, type StatsResponse } from '../api/client'
import DatabaseUpdateFlow from '../components/DatabaseUpdateFlow'
import DatabaseExportPanel from '../components/DatabaseExportPanel'
import ExportDeliveryBox, { type ExportDeliveryConfig } from '../components/ExportDeliveryBox'
import { DEFAULT_CONFIG_PATH, fromMacroPath, getProjectRoot, resolveMacroPath } from '../constants/config'

const preCls =
  'text-xs bg-gray-50 dark:bg-gray-900 p-3 rounded border border-gray-200 dark:border-gray-600 overflow-x-auto dark:text-gray-300'
const inputCls =
  'border border-gray-300 dark:border-gray-600 rounded px-2 py-1 text-sm bg-white dark:bg-gray-900 dark:text-gray-200'

const sectionTitleCls =
  'text-lg font-semibold text-gray-800 dark:text-gray-100 border-b border-gray-200 dark:border-gray-700 pb-2 mb-4'

function DatabasePageHelp({
  stats,
  loading,
  resolvedConfigPath,
}: {
  stats: StatsResponse | null
  loading: boolean
  resolvedConfigPath: string
}) {
  const [open, setOpen] = useState(false)
  const wrapRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onDocClick = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', onDocClick)
    return () => document.removeEventListener('mousedown', onDocClick)
  }, [open])

  return (
    <div ref={wrapRef} className="relative inline-flex align-middle">
      <button
        type="button"
        className="inline-flex h-5 w-5 items-center justify-center rounded-full border border-gray-300 bg-gray-100 text-[11px] font-bold text-gray-500 hover:bg-gray-200 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-400 dark:hover:bg-gray-700"
        aria-label="数据库页面说明"
        aria-expanded={open}
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onClick={() => setOpen(v => !v)}
      >
        ?
      </button>
      {open && (
        <div
          role="tooltip"
          className="absolute left-0 top-full z-20 mt-2 w-80 rounded-lg border border-gray-200 bg-white p-3 text-xs leading-relaxed text-gray-600 shadow-lg dark:border-gray-600 dark:bg-gray-900 dark:text-gray-300"
        >
          <p>进入本页自动加载数据库状态；也可按 F5 刷新。</p>
          <p className="mt-2">work_dir 由设置页 config 统一管理，本页不单独指定。</p>
          {stats && (
            <>
              <p className="mt-2">
                当前 work_dir：
                <code className="ml-1 font-mono text-[10px]">{stats.work_dir}</code>
                {loading && <span className="text-gray-400">（刷新中…）</span>}
              </p>
              <p className="mt-1">
                配置比对：
                <code className="ml-1 font-mono text-[10px] break-all">
                  {resolvedConfigPath || fromMacroPath(DEFAULT_CONFIG_PATH) || DEFAULT_CONFIG_PATH}
                </code>
              </p>
            </>
          )}
        </div>
      )}
    </div>
  )
}

function ChapterSection({
  title,
  children,
  defaultCollapsed = false,
}: {
  title: string
  children: ReactNode
  defaultCollapsed?: boolean
}) {
  const [open, setOpen] = useState(!defaultCollapsed)
  return (
    <section className="mb-10 last:mb-0">
      <h3
        className={`${sectionTitleCls} cursor-pointer select-none flex items-center gap-2`}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="text-xs text-blue-500 shrink-0">{open ? '▼' : '▶'}</span>
        {title}
      </h3>
      {open && children}
    </section>
  )
}

export default function Database() {
  const location = useLocation()
  const [stats, setStats] = useState<StatsResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [msg, setMsg] = useState('')
  const [resolvedConfigPath, setResolvedConfigPath] = useState('')

  const [recOffset, setRecOffset] = useState(0)
  const [recLimit, setRecLimit] = useState(30)
  const [recCid, setRecCid] = useState('')
  const [recResult, setRecResult] = useState<any>(null)
  const [dupResult, setDupResult] = useState<any>(null)
  const [recLoading, setRecLoading] = useState(false)
  const [dupLoading, setDupLoading] = useState(false)

  const [annoMode, setAnnoMode] = useState<'full' | 'incremental'>('full')
  const [centersOnly, setCentersOnly] = useState(false)
  const [maintBusy, setMaintBusy] = useState(false)
  const [deliveryConfig, setDeliveryConfig] = useState<ExportDeliveryConfig>({
    delivery: 'browser',
    localDir: '',
    validated: false,
    validatedPath: null,
  })
  const [clearDialog, setClearDialog] = useState<null | 'embeddings' | 'duplicates'>(null)
  const [clearBusy, setClearBusy] = useState(false)

  const showMsg = (text: string) => { setMsg(text); setTimeout(() => setMsg(''), 5000) }

  const loadStats = useCallback(async () => {
    setLoading(true)
    try {
      const configPath = await resolveMacroPath(DEFAULT_CONFIG_PATH)
      setResolvedConfigPath(configPath || getProjectRoot())
      const res = await api.databaseStats({
        config_path: configPath,
      })
      setStats(res)
    } catch (e: any) {
      showMsg(`加载失败: ${e.message}`)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (location.pathname === '/database') {
      void loadStats()
    }
  }, [location.pathname, loadStats])

  const runMaint = async (label: string, fn: () => Promise<unknown>) => {
    setMaintBusy(true)
    try {
      const res = await fn()
      showMsg(`${label}完成: ${JSON.stringify(res)}`)
      await loadStats()
    } catch (e: any) {
      showMsg(`${label}失败: ${e.message}`)
    } finally {
      setMaintBusy(false)
    }
  }

  const handleConfirmClear = async () => {
    if (!clearDialog) return
    setClearBusy(true)
    try {
      if (clearDialog === 'embeddings') {
        const res = await api.clearEmbeddings({})
        showMsg(`已清空向量索引（移除 ${res.removed_count ?? 0} 条）`)
        setRecResult(null)
      } else {
        const res = await api.clearDuplicates({})
        showMsg(
          res.removed
            ? `已清空近重复侧车：${res.file}`
            : '近重复侧车文件本就不存在，无需删除',
        )
        setDupResult(null)
      }
      setClearDialog(null)
      await loadStats()
    } catch (e: any) {
      showMsg(`清空失败: ${e.message}`)
    } finally {
      setClearBusy(false)
    }
  }

  const handleQueryRecords = async () => {
    setRecLoading(true)
    try {
      const res = await api.listRecords({
        offset: recOffset,
        limit: recLimit,
        cluster_id: recCid || undefined,
      })
      setRecResult(res)
    } catch (e: any) {
      showMsg(`查询失败: ${e.message}`)
    } finally {
      setRecLoading(false)
    }
  }

  const handleLoadDuplicates = async () => {
    setDupLoading(true)
    try {
      const res = await api.listDuplicates({ limit: 200 })
      setDupResult(res)
    } catch (e: any) {
      showMsg(`加载失败: ${e.message}`)
    } finally {
      setDupLoading(false)
    }
  }

  return (
    <div>
      <div className="mb-6 flex items-center gap-2">
        <h2 className="text-2xl font-semibold text-gray-800 dark:text-gray-100">数据库</h2>
        <DatabasePageHelp stats={stats} loading={loading} resolvedConfigPath={resolvedConfigPath} />
      </div>

      {loading && !stats && (
        <p className="mb-4 text-sm text-gray-500 dark:text-gray-400">正在加载数据库状态…</p>
      )}

      {msg && (
        <div className="mb-4 px-4 py-2 rounded text-sm bg-blue-50 dark:bg-blue-950/50 text-blue-700 dark:text-blue-300 border border-blue-200 dark:border-blue-800">
          {msg}
        </div>
      )}

      {stats && (
        <>
          {/* 第一章：状态 */}
          <ChapterSection title="状态">
            <div className="grid grid-cols-1 md:grid-cols-4 gap-4 mb-6">
              <StatCard label="索引内图片条数" value={stats.embedding_record_count || stats.chroma_document_count || 0} />
              <StatCard label="簇数量" value={stats.cluster_count} />
              <StatCard label="含非空标注的条数" value={stats.labeled_document_count} />
              <StatCard label="近重复对条数（侧车）" value={stats.duplicate_link_rows} />
            </div>

            <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
              <h4 className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-3">查询</h4>
              <p className="text-xs text-gray-500 dark:text-gray-400 mb-4">
                浏览索引记录或近重复侧车明细（只读）。
              </p>
              <h5 className="text-xs font-medium text-gray-600 dark:text-gray-400 mb-2">索引记录</h5>
              <div className="flex flex-wrap items-end gap-3 mb-4">
                <div>
                  <label className="block text-xs text-gray-500 dark:text-gray-400">offset</label>
                  <input type="number" value={recOffset} onChange={e => setRecOffset(Number(e.target.value))} className={`${inputCls} w-20`} />
                </div>
                <div>
                  <label className="block text-xs text-gray-500 dark:text-gray-400">limit</label>
                  <input type="number" value={recLimit} onChange={e => setRecLimit(Number(e.target.value))} className={`${inputCls} w-20`} />
                </div>
                <div>
                  <label className="block text-xs text-gray-500 dark:text-gray-400">cluster_id（可选）</label>
                  <input type="text" value={recCid} onChange={e => setRecCid(e.target.value)} className={`${inputCls} w-40 font-mono`} />
                </div>
                <button onClick={handleQueryRecords} disabled={recLoading} className="px-3 py-2 text-sm bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50">
                  {recLoading ? '查询中...' : '查询 records'}
                </button>
              </div>
              {recResult && <pre className={`${preCls} max-h-96`}>{JSON.stringify(recResult, null, 2)}</pre>}

              <hr className="my-4 border-gray-200 dark:border-gray-700" />
              <h5 className="text-xs font-medium text-gray-600 dark:text-gray-400 mb-2">近重复对（侧车）</h5>
              <button onClick={handleLoadDuplicates} disabled={dupLoading} className="px-3 py-2 text-sm bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50 mb-3">
                {dupLoading ? '加载中...' : '加载近重复侧车记录'}
              </button>
              {dupResult && <pre className={`${preCls} max-h-96`}>{JSON.stringify(dupResult, null, 2)}</pre>}
            </div>
          </ChapterSection>

          {/* 第二章：更新 */}
          <ChapterSection title="更新">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
              <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
                <h4 className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">上次成功任务写入的快照</h4>
                {stats.snapshot ? (
                  <details>
                    <summary className="text-xs text-blue-600 dark:text-blue-400 cursor-pointer">查看 JSON</summary>
                    <pre className={`mt-2 ${preCls}`}>{JSON.stringify(stats.snapshot, null, 2)}</pre>
                  </details>
                ) : (
                  <p className="text-xs text-gray-400 dark:text-gray-500">尚无 auto_tag_db_build_snapshot.json。</p>
                )}
              </div>
              <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
                <h4 className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">当前配置</h4>
                <details>
                  <summary className="text-xs text-blue-600 dark:text-blue-400 cursor-pointer">查看 JSON</summary>
                  <pre className={`mt-2 ${preCls}`}>{JSON.stringify(stats.current_params, null, 2)}</pre>
                </details>
              </div>
            </div>

            {!stats.has_config_diff && (
              <p className="text-xs text-green-600 dark:text-green-400 mb-4">快照与当前配置完全一致。</p>
            )}

            {stats.param_diff_table && stats.param_diff_table.length > 0 && (
              <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4 mb-6 overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="bg-gray-50 dark:bg-gray-900">
                      <th className="text-left px-3 py-2 text-gray-600 dark:text-gray-400 font-medium">参数</th>
                      <th className="text-left px-3 py-2 text-gray-600 dark:text-gray-400 font-medium">数据库快照(上次任务)</th>
                      <th className="text-left px-3 py-2 text-gray-600 dark:text-gray-400 font-medium">当前配置</th>
                    </tr>
                  </thead>
                  <tbody>
                    {stats.param_diff_table.map((row: any, i: number) => (
                      <tr key={i} className="border-t border-gray-100 dark:border-gray-700">
                        <td className="px-3 py-2 text-gray-700 dark:text-gray-300">{row['参数']}</td>
                        <td className="px-3 py-2 text-gray-500 dark:text-gray-400">{String(row['数据库快照(上次任务)'] ?? '—')}</td>
                        <td className="px-3 py-2 text-gray-500 dark:text-gray-400">{String(row['当前配置'] ?? '—')}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            <DatabaseUpdateFlow
              stats={stats}
              annoMode={annoMode}
              setAnnoMode={setAnnoMode}
              centersOnly={centersOnly}
              setCentersOnly={setCentersOnly}
              busy={maintBusy}
              onRebuild={() => runMaint('重建索引', () => api.rebuildRelations({}))}
              onRecompute={() => runMaint('重算关系', () => api.recomputeRelations({}))}
              onReannotate={() =>
                runMaint('更新标注', () =>
                  api.reannotate({
                    full_refresh: annoMode === 'full',
                    incremental: annoMode === 'incremental',
                    centers_only: centersOnly,
                  }),
                )
              }
            />
          </ChapterSection>

          {/* 第三章：导出 */}
          <ChapterSection title="导出">
            <div className="space-y-4">
              <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
                <h4 className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-3">下载方式</h4>
                <ExportDeliveryBox onChange={setDeliveryConfig} />
              </div>
              <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
                <h4 className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-3">导出内容</h4>
                <DatabaseExportPanel
                  recordCount={stats.embedding_record_count || stats.chroma_document_count || 0}
                  dupCount={stats.duplicate_link_rows || 0}
                  deliveryConfig={deliveryConfig}
                  onMessage={showMsg}
                />
              </div>
            </div>
          </ChapterSection>

          {/* 第四章：高级 */}
          <ChapterSection title="高级" defaultCollapsed>
            <div className="bg-white dark:bg-gray-800 rounded-lg border border-red-200 dark:border-red-900/50 p-4">
              <p className="text-sm text-gray-600 dark:text-gray-300 mb-1">危险操作</p>
              <p className="text-xs text-gray-500 dark:text-gray-400 mb-4">
                以下操作不可撤销。清空前请确认已不需要当前索引/侧车数据，或已完成导出备份。
                若有标注或维护任务正在运行，清空会被拒绝。
              </p>
              <div className="flex flex-wrap gap-3">
                <button
                  type="button"
                  disabled={maintBusy || clearBusy}
                  onClick={() => setClearDialog('embeddings')}
                  className="px-4 py-2 text-sm rounded border border-red-300 text-red-700 bg-red-50 hover:bg-red-100 disabled:opacity-50 dark:border-red-800 dark:text-red-300 dark:bg-red-950/40 dark:hover:bg-red-950/70"
                >
                  清空向量索引数据库
                </button>
                <button
                  type="button"
                  disabled={maintBusy || clearBusy}
                  onClick={() => setClearDialog('duplicates')}
                  className="px-4 py-2 text-sm rounded border border-red-300 text-red-700 bg-red-50 hover:bg-red-100 disabled:opacity-50 dark:border-red-800 dark:text-red-300 dark:bg-red-950/40 dark:hover:bg-red-950/70"
                >
                  清空近重复侧车库
                </button>
              </div>
              <p className="mt-3 text-xs text-gray-400 dark:text-gray-500">
                当前：索引 {stats.embedding_record_count || stats.chroma_document_count || 0} 条
                · 侧车 {stats.duplicate_link_rows || 0} 条
              </p>
            </div>
          </ChapterSection>
        </>
      )}

      {clearDialog && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/45 p-4">
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="clear-store-dialog-title"
            className="w-full max-w-md rounded-xl border border-gray-200 bg-white p-5 shadow-xl dark:border-gray-600 dark:bg-gray-800"
          >
            <h3
              id="clear-store-dialog-title"
              className="text-base font-semibold text-gray-900 dark:text-gray-100"
            >
              {clearDialog === 'embeddings' ? '确认清空向量索引？' : '确认清空近重复侧车？'}
            </h3>
            <div className="mt-3 space-y-2 text-sm leading-relaxed text-gray-600 dark:text-gray-300">
              {clearDialog === 'embeddings' ? (
                <>
                  <p>
                    将删除 work_dir 下向量索引集合中的<strong className="font-medium text-gray-800 dark:text-gray-100">全部记录</strong>
                    （约 {stats?.embedding_record_count || stats?.chroma_document_count || 0} 条），
                    标注与簇关系一并清除。
                  </p>
                  <p>近重复侧车文件不会被删除。此操作不可撤销。</p>
                </>
              ) : (
                <>
                  <p>
                    将删除 log 目录下的近重复侧车文件
                    （当前约 {stats?.duplicate_link_rows || 0} 条记录）。
                  </p>
                  <p>向量索引不会被改动。此操作不可撤销。</p>
                </>
              )}
            </div>
            <div className="mt-5 flex justify-end gap-2">
              <button
                type="button"
                disabled={clearBusy}
                onClick={() => setClearDialog(null)}
                className="rounded-lg border border-gray-300 px-4 py-2 text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-50 dark:border-gray-600 dark:text-gray-300 dark:hover:bg-gray-700"
              >
                取消
              </button>
              <button
                type="button"
                disabled={clearBusy}
                onClick={() => void handleConfirmClear()}
                className="rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white hover:bg-red-700 disabled:opacity-50"
              >
                {clearBusy ? '正在清空…' : '确认清空'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function StatCard({ label, value }: { label: string; value: number | undefined }) {
  const display = (value ?? 0).toLocaleString()
  return (
    <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
      <p className="text-xs text-gray-500 dark:text-gray-400 uppercase tracking-wide">{label}</p>
      <p className="text-2xl font-semibold text-gray-800 dark:text-gray-100 mt-1">{display}</p>
    </div>
  )
}
