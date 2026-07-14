import { useState, useEffect } from 'react'
import { api, type JobSummary } from '../api/client'
import { formatJobRuntime } from '../utils/jobDuration'

const STATUS_LABEL: Record<string, string> = {
  queued: '排队中',
  running: '运行中',
  done: '已完成',
  failed: '失败',
}

export default function TaskQuerySection() {
  const [jobs, setJobs] = useState<JobSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [sortDesc, setSortDesc] = useState(true)
  const [serverStartedAt, setServerStartedAt] = useState<number | null>(null)
  const [nowSec, setNowSec] = useState(() => Date.now() / 1000)

  const loadJobs = () => {
    setLoading(true)
    api.listJobs()
      .then(resp => {
        setJobs(resp.jobs || [])
        setServerStartedAt(resp.server_started_at ?? null)
      })
      .catch(() => setJobs([]))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    loadJobs()
  }, [])

  useEffect(() => {
    const hasRunning = jobs.some(j => j.status === 'running')
    if (!hasRunning) return
    const poll = setInterval(loadJobs, 3000)
    const tick = setInterval(() => setNowSec(Date.now() / 1000), 1000)
    return () => {
      clearInterval(poll)
      clearInterval(tick)
    }
  }, [jobs])

  const sorted = [...jobs].sort((a, b) => {
    const diff = (a.created_at || 0) - (b.created_at || 0)
    return sortDesc ? -diff : diff
  })

  return (
    <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
      <p className="text-xs text-gray-500 dark:text-gray-400 mb-4">
        展示后端记录的全部标注任务（含本次启动前已从磁盘恢复的历史）。总计 {jobs.length} 项。
        {serverStartedAt != null && serverStartedAt > 0 && (
          <span className="ml-2 text-gray-400">
            当前后端进程启动于 {new Date(serverStartedAt * 1000).toLocaleString()}。
          </span>
        )}
      </p>

      <div className="flex items-center gap-3 mb-4">
        <button
          type="button"
          onClick={loadJobs}
          disabled={loading}
          className="px-3 py-1.5 text-sm border border-gray-300 dark:border-gray-600 rounded hover:bg-gray-50 dark:hover:bg-gray-700 disabled:opacity-50 dark:text-gray-300"
        >
          {loading ? '刷新中…' : '刷新'}
        </button>
        <button
          type="button"
          onClick={() => setSortDesc(!sortDesc)}
          className="px-3 py-1.5 text-sm border border-gray-300 dark:border-gray-600 rounded hover:bg-gray-50 dark:hover:bg-gray-700 dark:text-gray-300"
        >
          {sortDesc ? '最新优先' : '最早优先'}
        </button>
      </div>

      {sorted.length === 0 && !loading && (
        <p className="text-sm text-gray-400 dark:text-gray-500">
          暂无任务记录。提交标注任务后会写入{' '}
          <code className="text-xs bg-gray-100 dark:bg-gray-800 px-1 rounded">work_dir/log/web_job_history.json</code>；
          若曾跑过任务仍为空，请确认 work_dir 与当时一致并点击刷新。
        </p>
      )}

      {sorted.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-sm border-collapse">
            <thead>
              <tr className="bg-gray-50 dark:bg-gray-900">
                <th className="text-left px-3 py-2 text-gray-600 dark:text-gray-400 font-medium">任务 ID</th>
                <th className="text-left px-3 py-2 text-gray-600 dark:text-gray-400 font-medium">状态</th>
                <th className="text-left px-3 py-2 text-gray-600 dark:text-gray-400 font-medium">创建时间</th>
                <th className="text-right px-3 py-2 text-gray-600 dark:text-gray-400 font-medium">耗时</th>
                <th className="text-left px-3 py-2 text-gray-600 dark:text-gray-400 font-medium">工作目录</th>
                <th className="text-right px-3 py-2 text-gray-600 dark:text-gray-400 font-medium">已收集</th>
                <th className="text-right px-3 py-2 text-gray-600 dark:text-gray-400 font-medium">已处理</th>
                <th className="text-right px-3 py-2 text-gray-600 dark:text-gray-400 font-medium">打标数</th>
                <th className="text-right px-3 py-2 text-gray-600 dark:text-gray-400 font-medium">跳过数</th>
                <th className="text-right px-3 py-2 text-gray-600 dark:text-gray-400 font-medium">失败数</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map(j => {
                const total = j.total || 0
                const proc = j.processed || 0
                const skipAll = (j.skip_in_db || 0) + (j.stage1_skips || 0) + (j.stage2_joins || 0)
                const den = proc || 1
                const vlmTotal = j.new_centers || 0
                const vlmDen = vlmTotal > 0 ? vlmTotal : den
                const ts = j.created_at ? new Date(j.created_at * 1000).toLocaleString() : '-'
                return (
                  <tr key={j.job_id} className="border-t border-gray-100 dark:border-gray-700">
                    <td className="px-3 py-2 text-xs font-mono text-gray-500 dark:text-gray-400">
                      {j.job_id.slice(0, 12)}
                    </td>
                    <td className="px-3 py-2">
                      <span
                        className={`text-xs px-2 py-0.5 rounded ${
                          j.status === 'done'
                            ? 'bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-300'
                            : j.status === 'running'
                              ? 'bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300'
                              : j.status === 'failed'
                                ? 'bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-300'
                                : 'bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-400'
                        }`}
                      >
                        {STATUS_LABEL[j.status] || j.status}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-xs text-gray-500 dark:text-gray-400">{ts}</td>
                    <td className="px-3 py-2 text-right text-xs text-gray-500 dark:text-gray-400 whitespace-nowrap">
                      {formatJobRuntime(j, nowSec)}
                    </td>
                    <td className="px-3 py-2 text-xs text-gray-500 dark:text-gray-400 max-w-40 truncate">
                      {j.work_dir || '-'}
                    </td>
                    <td className="px-3 py-2 text-right text-gray-600 dark:text-gray-400">{total || '-'}</td>
                    <td className="px-3 py-2 text-right text-gray-600 dark:text-gray-400">
                      {total > 0 ? `${proc} (${((proc / total) * 100).toFixed(0)}%)` : proc || '-'}
                    </td>
                    <td className="px-3 py-2 text-right text-gray-600 dark:text-gray-400">
                      {vlmDen > 0
                        ? `${j.vlm_calls || 0}/${vlmTotal > 0 ? vlmTotal : vlmDen} (${(((j.vlm_calls || 0) / vlmDen) * 100).toFixed(0)}%)`
                        : j.vlm_calls || 0}
                    </td>
                    <td className="px-3 py-2 text-right text-gray-600 dark:text-gray-400">
                      {den > 0 ? `${skipAll} (${((skipAll / den) * 100).toFixed(0)}%)` : skipAll || '-'}
                    </td>
                    <td className="px-3 py-2 text-right text-gray-600 dark:text-gray-400">
                      {den > 0
                        ? `${j.failed_so_far || 0} (${(((j.failed_so_far || 0) / den) * 100).toFixed(0)}%)`
                        : j.failed_so_far || 0}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
