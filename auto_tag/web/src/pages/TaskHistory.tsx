import { useState, useEffect } from 'react'
import { api, type JobSummary } from '../api/client'

const STATUS_LABEL: Record<string, string> = {
  queued: '排队中',
  running: '运行中',
  done: '已完成',
  failed: '失败',
}

export default function TaskHistory() {
  const [jobs, setJobs] = useState<JobSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [msg, setMsg] = useState('')
  const [sortDesc, setSortDesc] = useState(true)

  const showMsg = (text: string) => {
    setMsg(text)
    setTimeout(() => setMsg(''), 5000)
  }

  const loadJobs = () => {
    setLoading(true)
    api.listJobs()
      .then(resp => {
        setJobs(resp.jobs || [])
      })
      .catch((e: any) => showMsg(`拉取失败: ${e.message}`))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    loadJobs()
  }, [])

  const sorted = [...jobs].sort((a, b) => {
    const diff = (a.created_at || 0) - (b.created_at || 0)
    return sortDesc ? -diff : diff
  })

  return (
    <div>
      <h2 className="text-2xl font-semibold text-gray-800 dark:text-gray-100 mb-2">任务查询</h2>
      <p className="text-xs text-gray-500 dark:text-gray-400 mb-4">
        查看后端全部历史任务记录。总计 {jobs.length} 项。
      </p>

      {msg && <div className="mb-4 px-4 py-2 rounded text-sm bg-blue-50 text-blue-700 border border-blue-200">{msg}</div>}

      <section className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4 mb-6">
        <div className="flex items-center gap-3 mb-4">
          <button
            onClick={loadJobs}
            disabled={loading}
            className="px-3 py-1.5 text-sm border border-gray-300 rounded hover:bg-gray-50 disabled:opacity-50"
          >
            {loading ? '刷新中...' : '刷新'}
          </button>
          <button
            onClick={() => setSortDesc(!sortDesc)}
            className="px-3 py-1.5 text-sm border border-gray-300 rounded hover:bg-gray-50"
          >
            {sortDesc ? '最新优先' : '最早优先'}
          </button>
        </div>

        {sorted.length === 0 && !loading && (
          <p className="text-sm text-gray-400">暂无历史任务。（后端重启后内存中的任务记录会清空。）</p>
        )}

        {sorted.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-sm border-collapse">
              <thead>
                <tr className="bg-gray-50">
                  <th className="text-left px-3 py-2 text-gray-600 dark:text-gray-400 font-medium">任务 ID</th>
                  <th className="text-left px-3 py-2 text-gray-600 dark:text-gray-400 font-medium">状态</th>
                  <th className="text-left px-3 py-2 text-gray-600 dark:text-gray-400 font-medium">创建时间</th>
                  <th className="text-left px-3 py-2 text-gray-600 dark:text-gray-400 font-medium">工作目录</th>
                  <th className="text-right px-3 py-2 text-gray-600 dark:text-gray-400 font-medium">已收集</th>
                  <th className="text-right px-3 py-2 text-gray-600 dark:text-gray-400 font-medium">已处理</th>
                  <th className="text-right px-3 py-2 text-gray-600 dark:text-gray-400 font-medium">打标数</th>
                  <th className="text-right px-3 py-2 text-gray-600 dark:text-gray-400 font-medium">跳过数</th>
                  <th className="text-right px-3 py-2 text-gray-600 dark:text-gray-400 font-medium">失败数</th>
                </tr>
              </thead>
              <tbody>
                {sorted.map((j) => {
                  const total = j.total || 0
                  const proc = j.processed || 0
                  const skipAll = (j.skip_in_db || 0) + (j.stage1_skips || 0) + (j.stage2_joins || 0)
                  const den = proc || 1
                  const ts = j.created_at ? new Date(j.created_at * 1000).toLocaleString() : '-'
                  return (
                    <tr key={j.job_id} className="border-t border-gray-100">
                      <td className="px-3 py-2 text-xs font-mono text-gray-500">{j.job_id.slice(0, 12)}</td>
                      <td className="px-3 py-2">
                        <span className={`text-xs px-2 py-0.5 rounded ${
                          j.status === 'done' ? 'bg-green-100 text-green-700' :
                          j.status === 'running' ? 'bg-blue-100 text-blue-700' :
                          j.status === 'failed' ? 'bg-red-100 text-red-700' :
                          'bg-gray-100 text-gray-600'
                        }`}>
                          {STATUS_LABEL[j.status] || j.status}
                        </span>
                      </td>
                      <td className="px-3 py-2 text-xs text-gray-500">{ts}</td>
                      <td className="px-3 py-2 text-xs text-gray-500 max-w-40 truncate">{j.work_dir || '-'}</td>
                      <td className="px-3 py-2 text-right text-gray-600 dark:text-gray-400">{total || '-'}</td>
                      <td className="px-3 py-2 text-right text-gray-600 dark:text-gray-400">
                        {total > 0 ? `${proc} (${(proc / total * 100).toFixed(0)}%)` : proc || '-'}
                      </td>
                      <td className="px-3 py-2 text-right text-gray-600 dark:text-gray-400">
                        {den > 0 ? `${j.vlm_calls || 0} (${((j.vlm_calls || 0) / den * 100).toFixed(0)}%)` : (j.vlm_calls || 0)}
                      </td>
                      <td className="px-3 py-2 text-right text-gray-600 dark:text-gray-400">
                        {den > 0 ? `${skipAll} (${(skipAll / den * 100).toFixed(0)}%)` : skipAll || '-'}
                      </td>
                      <td className="px-3 py-2 text-right text-gray-600 dark:text-gray-400">
                        {den > 0 ? `${j.failed_so_far || 0} (${((j.failed_so_far || 0) / den * 100).toFixed(0)}%)` : (j.failed_so_far || 0)}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  )
}