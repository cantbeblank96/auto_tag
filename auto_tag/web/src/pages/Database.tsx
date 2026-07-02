import { useState } from 'react'
import { api, type StatsResponse } from '../api/client'

const EXPORT_MAX = 200_000

export default function Database() {
  const [stats, setStats] = useState<StatsResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [msg, setMsg] = useState('')

  // Record query state
  const [recOffset, setRecOffset] = useState(0)
  const [recLimit, setRecLimit] = useState(30)
  const [recCid, setRecCid] = useState('')
  const [recResult, setRecResult] = useState<any>(null)
  const [dupResult, setDupResult] = useState<any>(null)
  const [recLoading, setRecLoading] = useState(false)
  const [dupLoading, setDupLoading] = useState(false)
  const [exportCid, setExportCid] = useState('')
  const [exportLoading, setExportLoading] = useState<Record<string, boolean>>({})

  // Annotation mode
  const [annoMode, setAnnoMode] = useState<'full' | 'incremental'>('full')
  const [centersOnly, setCentersOnly] = useState(false)

  const showMsg = (text: string) => { setMsg(text); setTimeout(() => setMsg(''), 5000) }

  const handleRefresh = async () => {
    setLoading(true)
    try {
      const res = await api.databaseStats({})
      setStats(res)
    } catch (e: any) {
      showMsg(`刷新失败: ${e.message}`)
    } finally {
      setLoading(false)
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

  const handleExportCluster = async () => {
    if (!exportCid) { showMsg('请填写 cluster_id'); return }
    setExportLoading(p => ({ ...p, cluster: true }))
    try {
      await api.exportEmbeddings({ mode: 'cluster', cluster_id: exportCid })
    } catch (e: any) {
      showMsg(`导出失败: ${e.message}`)
    } finally {
      setExportLoading(p => ({ ...p, cluster: false }))
    }
  }

  const handleExportAction = async (key: string, fn: () => Promise<void>) => {
    setExportLoading(p => ({ ...p, [key]: true }))
    try {
      await fn()
    } catch (e: any) {
      showMsg(`导出失败: ${e.message}`)
    } finally {
      setExportLoading(p => ({ ...p, [key]: false }))
    }
  }

  return (
    <div>
      <h2 className="text-2xl font-semibold text-gray-800 dark:text-gray-100 mb-2">数据库</h2>
      <p className="text-xs text-gray-500 dark:text-gray-400 dark:text-gray-400 mb-4">
        同一 work_dir 下：向量索引目录、log 中的构建快照与近重复侧车表。
      </p>

      {msg && <div className="mb-4 px-4 py-2 rounded text-sm bg-blue-50 text-blue-700 border border-blue-200">{msg}</div>}

      {/* 刷新统计（work_dir 由设置页配置，后端自动读取 config.json） */}
      <section className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4 mb-6 max-w-lg">
        <button onClick={handleRefresh} disabled={loading} className="px-4 py-2 text-sm bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50">
          {loading ? '刷新中...' : '刷新状态'}
        </button>
      </section>

      {/* Stats cards */}
      {stats && (
        <>
          <div className="grid grid-cols-1 md:grid-cols-4 gap-4 mb-6">
            <StatCard label="索引内图片条数" value={stats.embedding_record_count || stats.chroma_document_count || 0} />
            <StatCard label="簇数量" value={stats.cluster_count} />
            <StatCard label="含非空标注的条数" value={stats.labeled_document_count} />
            <StatCard label="近重复对条数（侧车）" value={stats.duplicate_link_rows} />
          </div>

          {/* Snapshot vs Config */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
            <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
              <h3 className="text-sm font-medium text-gray-700 mb-2">上次成功任务写入的快照</h3>
              {stats.snapshot ? (
                <details open={stats.has_config_diff}>
                  <summary className="text-xs text-blue-600 cursor-pointer">查看 JSON</summary>
                  <pre className="mt-2 text-xs bg-gray-50 p-3 rounded border overflow-x-auto">{JSON.stringify(stats.snapshot, null, 2)}</pre>
                </details>
              ) : (
                <p className="text-xs text-gray-400">尚无 auto_tag_db_build_snapshot.json。</p>
              )}
            </div>
            <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
              <h3 className="text-sm font-medium text-gray-700 mb-2">当前配置</h3>
              <details open={stats.has_config_diff}>
                <summary className="text-xs text-blue-600 cursor-pointer">查看 JSON</summary>
                <pre className="mt-2 text-xs bg-gray-50 p-3 rounded border overflow-x-auto">{JSON.stringify(stats.current_params, null, 2)}</pre>
              </details>
            </div>
          </div>

          {!stats.has_config_diff && <p className="text-xs text-green-600 mb-4">完全一致。</p>}

          {/* Param diff table */}
          {stats.param_diff_table && stats.param_diff_table.length > 0 && (
            <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4 mb-6 overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="bg-gray-50">
                    <th className="text-left px-3 py-2 text-gray-600 font-medium">参数</th>
                    <th className="text-left px-3 py-2 text-gray-600 font-medium">数据库快照(上次任务)</th>
                    <th className="text-left px-3 py-2 text-gray-600 font-medium">当前配置</th>
                  </tr>
                </thead>
                <tbody>
                  {stats.param_diff_table.map((row: any, i: number) => (
                    <tr key={i} className="border-t border-gray-100">
                      <td className="px-3 py-2 text-gray-700">{row['参数']}</td>
                      <td className="px-3 py-2 text-gray-500 dark:text-gray-400">{String(row['数据库快照(上次任务)'] ?? '—')}</td>
                      <td className="px-3 py-2 text-gray-500 dark:text-gray-400">{String(row['当前配置'] ?? '—')}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* Actions */}
          <section className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4 mb-6">
            <h3 className="text-sm font-medium text-gray-700 mb-3">更新</h3>
            <div className="flex flex-wrap gap-3">
              <button onClick={async () => {
                try {
                  const res = await api.rebuildRelations({})
                  showMsg(`重建索引完成: ${JSON.stringify(res)}`)
                } catch (e: any) { showMsg(`重建失败: ${e.message}`) }
              }} className="px-3 py-2 text-sm bg-orange-500 text-white rounded hover:bg-orange-600">
                完全重建索引
              </button>
              <button onClick={async () => {
                try {
                  const res = await api.recomputeRelations({})
                  showMsg(`重算关系完成: ${JSON.stringify(res)}`)
                } catch (e: any) { showMsg(`重算失败: ${e.message}`) }
              }} disabled={!stats.enable_recompute_relations} className="px-3 py-2 text-sm bg-yellow-500 text-white rounded hover:bg-yellow-600 disabled:opacity-50">
                仅重算关系
              </button>
              <div className="flex items-center gap-2">
                <select value={annoMode} onChange={e => setAnnoMode(e.target.value as 'full' | 'incremental')}
                  className="border rounded px-2 py-1.5 text-sm" disabled={!stats.enable_reannotate}>
                  <option value="full">全量：按当前 questions 整图重标</option>
                  <option value="incremental">增量：仅为缺失的键补充</option>
                </select>
                <label className="flex items-center gap-1 text-sm">
                  <input type="checkbox" checked={centersOnly} onChange={e => setCentersOnly(e.target.checked)} disabled={!stats.enable_reannotate} />
                  仅簇中心调 VLM
                </label>
                <button onClick={async () => {
                  try {
                    const res = await api.reannotate({
                      full_refresh: annoMode === 'full',
                      incremental: annoMode === 'incremental',
                      centers_only: centersOnly,
                    })
                    showMsg(`更新标注完成: ${JSON.stringify(res)}`)
                  } catch (e: any) { showMsg(`更新失败: ${e.message}`) }
                }} disabled={!stats.enable_reannotate} className="px-3 py-2 text-sm bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50">
                  更新标注（questions）
                </button>
              </div>
            </div>
          </section>

          {/* Query Records */}
          <section className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4 mb-6">
            <h3 className="text-sm font-medium text-gray-700 mb-3">查询</h3>
            <h4 className="text-xs font-medium text-gray-600 mb-2">索引记录</h4>
            <div className="flex flex-wrap items-end gap-3 mb-4">
              <div><label className="block text-xs text-gray-500 dark:text-gray-400">offset</label><input type="number" value={recOffset} onChange={e => setRecOffset(Number(e.target.value))} className="border rounded px-2 py-1 text-sm w-20" /></div>
              <div><label className="block text-xs text-gray-500 dark:text-gray-400">limit</label><input type="number" value={recLimit} onChange={e => setRecLimit(Number(e.target.value))} className="border rounded px-2 py-1 text-sm w-20" /></div>
              <div><label className="block text-xs text-gray-500 dark:text-gray-400">cluster_id（可选）</label><input type="text" value={recCid} onChange={e => setRecCid(e.target.value)} className="border rounded px-2 py-1 text-sm w-40 font-mono" /></div>
              <button onClick={handleQueryRecords} disabled={recLoading} className="px-3 py-2 text-sm bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50">{recLoading ? '查询中...' : '查询 records'}</button>
            </div>
            {recResult && <pre className="text-xs bg-gray-50 p-3 rounded border overflow-x-auto max-h-96">{JSON.stringify(recResult, null, 2)}</pre>}

            <hr className="my-4" />
            <h4 className="text-xs font-medium text-gray-600 mb-2">近重复对（侧车）</h4>
            <button onClick={handleLoadDuplicates} disabled={dupLoading} className="px-3 py-2 text-sm bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50 mb-3">{dupLoading ? '加载中...' : '加载近重复侧车记录'}</button>
            {dupResult && <pre className="text-xs bg-gray-50 p-3 rounded border overflow-x-auto max-h-96">{JSON.stringify(dupResult, null, 2)}</pre>}
          </section>

          {/* Export */}
          <section className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4 mb-6">
            <h3 className="text-sm font-medium text-gray-700 mb-3">导出</h3>

            {/* Export tabs */}
            <div className="space-y-6">
              {/* Embeddings export */}
              <div>
                <h4 className="text-xs font-medium text-gray-600 mb-2">索引</h4>
                <div className="flex flex-wrap gap-2">
                  <button onClick={() => api.exportEmbeddings({ mode: 'range', offset: 0, limit: EXPORT_MAX })}
                    className="px-3 py-1.5 text-sm border border-gray-300 rounded hover:bg-gray-50">按 offset/limit</button>
                  <div className="flex items-center gap-1">
                    <input type="text" placeholder="cluster_id" value={exportCid} onChange={e => setExportCid(e.target.value)} className="border rounded px-2 py-1 text-sm w-32 font-mono" />
                    <button onClick={handleExportCluster} disabled={exportLoading.cluster} className="px-3 py-1.5 text-sm border border-gray-300 rounded hover:bg-gray-50 disabled:opacity-50">{exportLoading.cluster ? '导出中...' : '按 cluster'}</button>
                  </div>
                  <button onClick={() => api.exportEmbeddings({ mode: 'chunk', chunk_index: 0, chunk_size: EXPORT_MAX })}
                    className="px-3 py-1.5 text-sm border border-gray-300 rounded hover:bg-gray-50">分块下载</button>
                </div>
              </div>

              {/* Duplicates export */}
              <div>
                <h4 className="text-xs font-medium text-gray-600 mb-2">侧车</h4>
                <div className="flex flex-wrap gap-2">
                  <button onClick={() => api.exportDuplicates({ mode: 'range', offset: 0, limit: EXPORT_MAX })}
                    className="px-3 py-1.5 text-sm border border-gray-300 rounded hover:bg-gray-50">按 offset/limit</button>
                  <button onClick={() => api.exportDuplicates({ mode: 'chunk', chunk_index: 0, chunk_size: EXPORT_MAX })}
                    className="px-3 py-1.5 text-sm border border-gray-300 rounded hover:bg-gray-50">分块下载</button>
                </div>
              </div>

              {/* Compact labels export */}
              <div>
                <h4 className="text-xs font-medium text-gray-600 mb-2">标注</h4>
                <div className="flex flex-wrap gap-2">
                  <button onClick={() => api.exportCompactShared({})}
                    className="px-3 py-1.5 text-sm border border-gray-300 rounded hover:bg-gray-50">共享字典</button>
                  <button onClick={() => api.exportCompactSlice({ offset: 0, limit: EXPORT_MAX })}
                    className="px-3 py-1.5 text-sm border border-gray-300 rounded hover:bg-gray-50">offset/limit</button>
                  <button onClick={() => api.exportCompactChunk({ chunk_index: 0, chunk_size: EXPORT_MAX })}
                    className="px-3 py-1.5 text-sm border border-gray-300 rounded hover:bg-gray-50">分块下载</button>
                </div>
              </div>
            </div>
          </section>
        </>
      )}
    </div>
  )
}

function StatCard({ label, value }: { label: string; value: number }) {
  return (
    <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
      <p className="text-xs text-gray-500 dark:text-gray-400 uppercase tracking-wide">{label}</p>
      <p className="text-2xl font-semibold text-gray-800 mt-1">{value.toLocaleString()}</p>
    </div>
  )
}