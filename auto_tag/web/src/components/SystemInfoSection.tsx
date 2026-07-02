import { useState } from 'react'
import { api, type HealthResponse } from '../api/client'
import { formatAppVersion } from '../constants/version'
import { waitForBackendHealthy } from '../utils/backendHealth'

const cardCls =
  'bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4 mb-4 max-w-3xl'

/** 健康检查、版本与联系信息。 */
export default function SystemInfoSection() {
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [restarting, setRestarting] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  const showNotice = (text: string, isError = false) => {
    setNotice(text)
    setError(isError ? text : '')
    if (!isError) setError('')
    setTimeout(() => setNotice(''), 5000)
  }

  const checkHealth = async () => {
    setLoading(true)
    setError('')
    try {
      const res = await api.health()
      setHealth(res)
      showNotice('健康检查成功')
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e)
      setError(msg)
      setHealth(null)
    } finally {
      setLoading(false)
    }
  }

  const handleRestartBackend = async () => {
    setError('')
    try {
      const status = await api.backendStatus()
      if (status.active_job_count > 0) {
        const ok = window.confirm(
          `当前有 ${status.active_job_count} 个标注/维护任务正在进行。重启会立即中断这些任务，是否继续？`,
        )
        if (!ok) return
      }
    } catch {
      /* 状态接口不可用时仍允许尝试重启 */
    }

    setRestarting(true)
    try {
      await api.restartBackend()
      const ok = await waitForBackendHealthy()
      if (!ok) throw new Error('后端重启后未能及时恢复，请手动执行 run_web_backend.sh')
      const res = await api.health()
      setHealth(res)
      showNotice('后端已重启，配置已重新加载')
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e)
      setError(msg)
    } finally {
      setRestarting(false)
    }
  }

  return (
    <div>
      <section className={cardCls}>
        <h4 className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-3">服务状态</h4>
        <p className="text-sm text-gray-600 dark:text-gray-400 mb-3">
          检查 FastAPI 后端是否可达，或重启后端以从磁盘重新加载 config.json（会中断进行中的任务）。
        </p>
        <div className="flex flex-wrap gap-2">
          <button
            onClick={checkHealth}
            disabled={loading || restarting}
            className="px-4 py-2 text-sm bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50"
          >
            {loading ? '检查中...' : '检查 API'}
          </button>
          <button
            onClick={handleRestartBackend}
            disabled={loading || restarting}
            className="px-4 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded hover:bg-gray-50 dark:hover:bg-gray-700 disabled:opacity-50 dark:text-gray-200"
          >
            {restarting ? '重启中...' : '重启后端'}
          </button>
        </div>
        {notice && !error && (
          <p className="mt-2 text-sm text-green-600 dark:text-green-400">{notice}</p>
        )}
        {error && <p className="mt-2 text-sm text-red-600 dark:text-red-400">{error}</p>}
        {health && (
          <pre className="mt-3 text-xs bg-gray-50 dark:bg-gray-900 p-3 rounded border dark:border-gray-600 overflow-x-auto dark:text-gray-300">
            {JSON.stringify(health, null, 2)}
          </pre>
        )}
      </section>

      <section className={cardCls}>
        <h4 className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-3">关于</h4>
        <div className="space-y-2 text-sm text-gray-600 dark:text-gray-400">
          <p>
            Auto Tag 是基于 CLIP + ChromaDB + VLM 的图像自动标注流水线，提供 Web 控制台与命令行两种使用方式。
          </p>
          <p>
            联系作者：
            <a
              href="mailto:xukaiming1996@163.com"
              className="text-blue-600 dark:text-blue-400 hover:underline ml-1"
            >
              xukaiming1996@163.com
            </a>
          </p>
          <p>
            软件版本：<strong className="text-gray-800 dark:text-gray-200">{formatAppVersion()}</strong>
          </p>
        </div>
      </section>
    </div>
  )
}
