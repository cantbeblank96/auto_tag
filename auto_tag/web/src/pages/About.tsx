import { useState } from 'react'
import { api, type HealthResponse } from '../api/client'

export default function About() {
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const checkHealth = async () => {
    setLoading(true)
    setError('')
    try {
      const res = await api.health()
      setHealth(res)
    } catch (e: any) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  const cardCls = "bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4 mb-6 max-w-lg"

  return (
    <div>
      <h2 className="text-2xl font-semibold text-gray-800 dark:text-gray-100 mb-6">其他</h2>

      <section className={cardCls}>
        <h3 className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-3">健康检查</h3>
        <button
          onClick={checkHealth}
          disabled={loading}
          className="px-4 py-2 text-sm bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50"
        >
          {loading ? '检查中...' : '检查 API'}
        </button>
        {error && <p className="mt-2 text-sm text-red-600 dark:text-red-400">{error}</p>}
        {health && (
          <pre className="mt-3 text-xs bg-gray-50 dark:bg-gray-900 p-3 rounded border dark:border-gray-600 overflow-x-auto dark:text-gray-300">
            {JSON.stringify(health, null, 2)}
          </pre>
        )}
      </section>

      <section className={cardCls}>
        <h3 className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-3">关于</h3>
        <div className="space-y-2 text-sm text-gray-600 dark:text-gray-400">
          <p>联系作者：<a href="mailto:xukaiming1996@163.com" className="text-blue-600 dark:text-blue-400 hover:underline">xukaiming1996@163.com</a></p>
          <p>软件版本：<strong className="text-gray-800 dark:text-gray-200">0.0</strong></p>
        </div>
      </section>
    </div>
  )
}