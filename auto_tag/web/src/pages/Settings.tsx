import { useState, useEffect } from 'react'
import { api } from '../api/client'

const PROJECT_ROOT = '/home/SENSETIME/xukaiming/Desktop/my_repos/python_projects/kevin_auto_tag/auto_tag'
const PROJECT_PATH_MACRO = '{PROJECT_PATH}'
const DEFAULT_CONFIG_PATH = `${PROJECT_PATH_MACRO}/config.json`

function fromMacroPath(macro: string): string {
  return macro.replace(PROJECT_PATH_MACRO, PROJECT_ROOT)
}

// ── Types ──────────────────────────────────────────────

interface QuestionEntry {
  key: string
  enabled: boolean
  _mode: 'template' | 'freeform'
  description: string
  type: string
  choices: string
  min: string
  max: string
  step: string
  freeformJson: string
}

interface ModelEntry {
  name: string; base_url: string | null; api_key: string; priority: number; enabled?: boolean
  tripped?: boolean; failures_in_window?: number; total_calls?: number
  failure_rate?: number; last_error?: string
}

type QuestionDetail = Record<string, any>

// ── Helpers ────────────────────────────────────────────

function questionToDetail(q: QuestionEntry): QuestionDetail | null {
  if (!q.enabled) return null
  if (q._mode === 'freeform') {
    try { return JSON.parse(q.freeformJson) } catch { return null }
  }
  const d: QuestionDetail = { description: q.description, type: q.type }
  if (q.type === 'category' && q.choices.trim()) {
    d.choices = q.choices.split('\n').map(s => s.trim()).filter(Boolean)
  }
  if (q.type === 'int' || q.type === 'float') {
    if (q.min) d.min = Number(q.min)
    if (q.max) d.max = q.max ? Number(q.max) : null
    if (q.type === 'float' && q.step) d.step = Number(q.step)
  }
  return d
}

function detailToQuestion(key: string, detail: QuestionDetail): QuestionEntry {
  const t = String(detail.type || 'string')
  return {
    key, enabled: true, _mode: 'template',
    description: String(detail.description || ''),
    type: t,
    choices: Array.isArray(detail.choices) ? detail.choices.join('\n') : '',
    min: detail.min != null ? String(detail.min) : '',
    max: detail.max != null ? String(detail.max) : '',
    step: detail.step != null ? String(detail.step) : '',
    freeformJson: JSON.stringify(detail, null, 2),
  }
}

function emptyQuestion(): QuestionEntry {
  return { key: '', enabled: true, _mode: 'template', description: '', type: 'string', choices: '', min: '', max: '', step: '', freeformJson: '{}' }
}

// ── Component ──────────────────────────────────────────

export default function Settings() {
  const [configPath] = useState(DEFAULT_CONFIG_PATH)
  const [msg, setMsg] = useState('')
  const [msgType, setMsgType] = useState<'success' | 'error'>('success')

  // Model management (unchanged)
  const [models, setModels] = useState<ModelEntry[]>([])
  const [vlmStrategy, setVlmStrategy] = useState('priority')
  const [cbTimeWindow, setCbTimeWindow] = useState(300)
  const [cbFailureThreshold, setCbFailureThreshold] = useState(0.5)
  const [cbCooldown, setCbCooldown] = useState(600)
  const [testingModels, setTestingModels] = useState<Record<string, boolean>>({})
  const [testResults, setTestResults] = useState<Record<string, { ok: boolean; latency_ms?: number; error?: string }>>({})
  const [isDirty, setIsDirty] = useState(false)
  const markDirty = () => setIsDirty(true)

  // General settings (from config.json)
  const [batchSize, setBatchSize] = useState(32)
  const [tauDup, setTauDup] = useState(0.05)
  const [tauCls, setTauCls] = useState(0.25)
  const [workDir, setWorkDir] = useState(`${PROJECT_PATH_MACRO}/work_dir`)
  const [recDup, setRecDup] = useState(true)

  // Questions
  const [questions, setQuestions] = useState<QuestionEntry[]>([])
  const [questionSearch, setQuestionSearch] = useState('')

  useEffect(() => { loadEverything() }, [])

  const showMsg = (text: string, type: 'success' | 'error' = 'success') => {
    setMsg(text); setMsgType(type); setTimeout(() => setMsg(''), 5000)
  }

  const loadEverything = async () => {
    // Load config.json
    try {
      const p = fromMacroPath(configPath)
      const res = await fetch(`/api/utils/read_file?path=${encodeURIComponent(p)}`)
      if (res.ok) {
        const data = await res.json()
        const cfg = typeof data.content === 'string' ? JSON.parse(data.content) : data.content
        setBatchSize(cfg.batch_size ?? 32)
        setTauDup(cfg.tau_dup ?? 0.05)
        setTauCls(cfg.tau_cls ?? 0.25)
        setWorkDir(cfg.work_dir ?? `${PROJECT_PATH_MACRO}/work_dir`)
        setRecDup(cfg.record_stage1_duplicates ?? true)
        // Load questions
        const qs = cfg.questions || {}
        setQuestions(Object.entries(qs).map(([k, v]) => detailToQuestion(k, v as QuestionDetail)))
        // Load models from config.json (not from API, avoids stale in-memory state)
        const rawModels = cfg.vlm_models || []
        if (rawModels.length > 0) {
          setModels(rawModels.map((m: any, i: number) => ({ ...m, priority: m.priority ?? (i + 1) })))
        } else {
          // Fallback: single model from .env
          setModels([{ name: 'None', base_url: null, api_key: '', priority: 1 }])
        }
        setVlmStrategy(cfg.vlm_strategy || 'priority')
        // Circuit breaker config
        const cb = cfg.circuit_breaker || {}
        setCbTimeWindow(cb.time_window_seconds ?? 300)
        setCbFailureThreshold(cb.failure_rate_threshold ?? 0.5)
        setCbCooldown(cb.cooldown_seconds ?? 600)
      }
    } catch { /* ignore */ }
  }

  const saveAll = async () => {
    const p = fromMacroPath(configPath)
    // Build config
    const questionsObj: Record<string, any> = {}
    for (const q of questions) {
      if (!q.key.trim()) continue
      const detail = questionToDetail(q)
      if (detail) questionsObj[q.key.trim()] = detail
    }
    const cfg = {
      work_dir: workDir,
      batch_size: batchSize,
      tau_dup: tauDup,
      tau_cls: tauCls,
      embedding_subdir: 'embedding_index',
      record_stage1_duplicates: recDup,
      duplicate_links_filename: 'duplicate_links.sqlite',
      vlm_models: models.map(m => ({
        name: m.name, base_url: m.base_url, api_key: m.api_key, priority: m.priority, enabled: m.enabled,
      })),
      questions: questionsObj,
      vlm_strategy: vlmStrategy,
      circuit_breaker: {
        time_window_seconds: cbTimeWindow,
        failure_rate_threshold: cbFailureThreshold,
        cooldown_seconds: cbCooldown,
      },
    }
    try {
      const res = await fetch('/api/utils/write_file', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: p, content: JSON.stringify(cfg, null, 4) }),
      })
      if (!res.ok) throw new Error((await res.json()).detail || 'write failed')
      showMsg('所有设置已保存')
      setIsDirty(false)
    } catch (e: any) { showMsg(`保存失败: ${e.message}`, 'error') }
  }

  // Questions handlers
  const addQuestion = () => {
    setQuestions([...questions, emptyQuestion()])
    markDirty()
  }
  const removeQuestion = (idx: number) => {
    setQuestions(questions.filter((_, i) => i !== idx))
    markDirty()
  }
  const updateQuestion = (idx: number, patch: Partial<QuestionEntry>) => {
    const next = [...questions]
    next[idx] = { ...next[idx], ...patch }
    setQuestions(next)
    markDirty()
  }
  const toggleQuestionEnabled = (idx: number) => {
    const next = [...questions]
    next[idx] = { ...next[idx], enabled: !next[idx].enabled }
    setQuestions(next)
    markDirty()
  }

  // Model handlers
  const addModel = () => {
    setModels([...models, {
      name: '', base_url: null, api_key: '', priority: models.length + 1, enabled: true,
    }])
    markDirty()
  }
  const removeModel = (idx: number) => {
    setModels(models.filter((_, i) => i !== idx).map((m, i) => ({ ...m, priority: i + 1 })))
    markDirty()
  }
  const updateModel = (idx: number, field: keyof ModelEntry, value: any) => {
    const next = [...models]
    next[idx] = { ...next[idx], [field]: value }
    setModels(next)
    markDirty()
  }
  const toggleModelEnabled = (idx: number) => {
    const next = [...models]
    const current = next[idx].enabled ?? true
    next[idx] = { ...next[idx], enabled: !current }
    setModels(next)
    markDirty()
  }
  const moveModel = (idx: number, dir: -1 | 1) => {
    const target = idx + dir
    if (target < 0 || target >= models.length) return
    const next = [...models]
    ;[next[idx], next[target]] = [next[target], next[idx]]
    setModels(next.map((m, i) => ({ ...m, priority: i + 1 })))
    markDirty()
  }
  const handleTestModel = async (name: string) => {
    if (!name) { showMsg('请先填写模型名称', 'error'); return }
    setTestingModels(p => ({ ...p, [name]: true }))
    try { const res = await api.testModel(name); setTestResults(p => ({ ...p, [name]: res })) }
    catch (e: any) { setTestResults(p => ({ ...p, [name]: { ok: false, error: e.message } })) }
    finally { setTestingModels(p => ({ ...p, [name]: false })) }
  }

  /** 判断自由形式 JSON 是否包含模版必需的字段，从而能否被形式校验。 */
  function isFreeformValidatable(freeformJson: string): boolean {
    try {
      const obj = JSON.parse(freeformJson)
      if (!obj || typeof obj !== 'object') return false
      if (!obj.description || !obj.type) return false
      const validTypes = ['string', 'category', 'int', 'float']
      if (!validTypes.includes(obj.type)) return false
      if (obj.type === 'category' && (!Array.isArray(obj.choices) || obj.choices.length === 0)) return false
      return true
    } catch { return false }
  }

  const inputCls = "w-full border rounded px-3 py-2 text-sm font-mono dark:bg-gray-800 dark:border-gray-600 dark:text-gray-200"
  const labelCls = "block text-xs text-gray-500 dark:text-gray-400 mb-0.5"
  const sectionCls = "bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4"

  return (
    <div>
      <h2 className="text-2xl font-semibold text-gray-800 dark:text-gray-100 mb-6">设置</h2>

      {msg && (
        <div className={`mb-4 px-4 py-2 rounded text-sm ${msgType === 'success' ? 'bg-green-50 dark:bg-green-900/30 text-green-700 dark:text-green-300 border border-green-200 dark:border-green-800' : 'bg-red-50 dark:bg-red-900/30 text-red-700 dark:text-red-300 border border-red-200 dark:border-red-800'}`}>
          {msg}
        </div>
      )}

      <div className="space-y-6 max-w-3xl">
        {/* ═══ VLM 模型管理 ═══ */}
        <section className={sectionCls}>
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-medium text-gray-700 dark:text-gray-300">VLM 模型管理</h3>
            <div className="flex gap-2">
              <button onClick={addModel} className="px-3 py-1.5 text-xs bg-blue-600 text-white rounded hover:bg-blue-700">+ 添加模型</button>
            </div>
          </div>
          <div className="flex items-center gap-4 mb-3 p-3 bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded-lg">
            <label className="text-sm font-medium text-gray-700 dark:text-gray-300">调用策略</label>
            <select value={vlmStrategy} onChange={e => { setVlmStrategy(e.target.value); markDirty() }} className="border rounded px-3 py-1.5 text-sm bg-white dark:bg-gray-800 dark:border-gray-600 dark:text-gray-200">
              <option value="priority">优先级顺序（Failover）</option>
              <option value="round_robin">均衡负载（Round-Robin）</option>
            </select>
          </div>
          <p className="text-xs text-gray-400 dark:text-gray-500 mb-3">配置保存在：<code className="bg-gray-100 dark:bg-gray-700 px-1 rounded">{configPath}</code></p>
          {models.length === 0 && <p className="text-xs text-gray-400 py-4 text-center">暂无模型。点击「+ 添加模型」添加。</p>}
          <div className="space-y-3 max-h-[26rem] overflow-y-auto pr-1">
            {models.map((m, idx) => (
              <div key={idx} className={`border rounded-lg p-3 ${(m.enabled ?? true) ? 'border-gray-200 dark:border-gray-600 bg-gray-50 dark:bg-gray-700/50' : 'border-gray-200 dark:border-gray-700 bg-gray-100/50 dark:bg-gray-800/30 opacity-60'}`}>
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs font-medium text-gray-500 dark:text-gray-400"># {m.priority}</span>
                  <div className="flex gap-1">
                    <button onClick={() => moveModel(idx, -1)} disabled={idx === 0} className="px-2 py-0.5 text-xs border rounded hover:bg-gray-100 dark:hover:bg-gray-600 disabled:opacity-30 dark:text-gray-300 dark:border-gray-600">↑</button>
                    <button onClick={() => moveModel(idx, 1)} disabled={idx === models.length - 1} className="px-2 py-0.5 text-xs border rounded hover:bg-gray-100 dark:hover:bg-gray-600 disabled:opacity-30 dark:text-gray-300 dark:border-gray-600">↓</button>
                    <button onClick={() => toggleModelEnabled(idx)} className={`px-2 py-0.5 text-xs rounded ${(m.enabled ?? true) ? 'bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-300' : 'bg-gray-200 dark:bg-gray-600 text-gray-500 dark:text-gray-400'}`}>{m.enabled !== false ? '启用' : '暂停'}</button>
                    <button onClick={() => removeModel(idx)} className="px-2 py-0.5 text-xs text-red-600 border border-red-200 rounded hover:bg-red-50 dark:text-red-400 dark:border-red-800 dark:hover:bg-red-900/30">删除</button>
                  </div>
                </div>
                {(m.enabled ?? true) && (<>
                <div className="grid grid-cols-3 gap-3">
                  <div><label className={labelCls}>模型名称</label><input type="text" value={m.name} onChange={e => updateModel(idx, 'name', e.target.value)} placeholder="gemini/gemini-2.5-flash" className={inputCls} /></div>
                  <div><label className={labelCls}>Base URL（可选）</label><input type="text" value={m.base_url || ''} onChange={e => updateModel(idx, 'base_url', e.target.value || null)} placeholder="https://..." className={inputCls} /></div>
                  <div><label className={labelCls}>API Key</label><input type="password" value={m.api_key} onChange={e => updateModel(idx, 'api_key', e.target.value)} placeholder="your-api-key" className={inputCls} /></div>
                </div>
                {m.tripped !== undefined && (
                  <div className="mt-2 flex items-center gap-3 text-xs">
                    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full ${m.tripped ? 'bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-300' : 'bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-300'}`}>
                      <span className={`w-1.5 h-1.5 rounded-full ${m.tripped ? 'bg-red-500' : 'bg-green-500'}`} />{m.tripped ? '已熔断' : '正常'}
                    </span>
                    <span className="text-gray-400 dark:text-gray-500">调用 {m.total_calls || 0} 次</span>
                    <span className="text-gray-400 dark:text-gray-500">失败 {(m.failures_in_window || 0)} 次</span>
                    {m.last_error && <span className="text-red-400 truncate max-w-48" title={m.last_error}>{m.last_error}</span>}
                  </div>
                )}
                {testResults[m.name] && (
                  <div className={`mt-1 text-xs ${testResults[m.name].ok ? 'text-green-600 dark:text-green-400' : 'text-red-600 dark:text-red-400'}`}>
                    {testResults[m.name].ok ? `连通成功 (${testResults[m.name].latency_ms}ms)` : `连通失败: ${testResults[m.name].error}`}
                  </div>
                )}
                <button onClick={() => handleTestModel(m.name)} disabled={testingModels[m.name] || !m.name} className="mt-2 px-3 py-1 text-xs border border-gray-300 dark:border-gray-600 rounded hover:bg-gray-100 dark:hover:bg-gray-600 disabled:opacity-50 dark:text-gray-300">{testingModels[m.name] ? '测试中...' : '测试连通'}</button>
                </>)}
              </div>
            ))}
          </div>
          {models.length > 0 && (
            <div className="mt-3">
              <button onClick={async () => { try { await api.resetCircuitBreaker(); showMsg('已重置所有模型熔断状态') } catch (e: any) { showMsg(`重置失败: ${e.message}`, 'error') } }} className="px-4 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded hover:bg-gray-50 dark:hover:bg-gray-700 dark:text-gray-300">重置全部熔断</button>
            </div>
          )}
        </section>

        {/* ═══ 熔断参数 ═══ */}
        <section className={sectionCls}>
          <h3 className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-3">熔断参数</h3>
          <p className="text-xs text-gray-400 dark:text-gray-500 mb-3">在指定时间窗口内，若失败率超过阈值则自动停用该模型，等待停用时长后恢复。</p>
          <div className="grid grid-cols-3 gap-4">
            <div><label className={labelCls}>时间窗口（秒）</label><input type="number" value={cbTimeWindow} onChange={e => { setCbTimeWindow(Number(e.target.value)); markDirty() }} min={10} className={inputCls} /></div>
            <div><label className={labelCls}>失败率阈值</label><input type="number" value={cbFailureThreshold} onChange={e => { setCbFailureThreshold(Number(e.target.value)); markDirty() }} min={0} max={1} step={0.05} className={inputCls} /></div>
            <div><label className={labelCls}>停用时长（秒）</label><input type="number" value={cbCooldown} onChange={e => { setCbCooldown(Number(e.target.value)); markDirty() }} min={10} className={inputCls} /></div>
          </div>
        </section>

        {/* ═══ 通用设置 ═══ */}
        <section className={sectionCls}>
          <h3 className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-3">通用设置</h3>
          <div className="grid grid-cols-2 gap-4">
            <div><label className={labelCls}>batch_size</label><input type="number" value={batchSize} onChange={e => { setBatchSize(Number(e.target.value)); markDirty() }} min={1} className={inputCls} /></div>
            <div><label className={labelCls}>tau_dup（去重阈值）</label><input type="number" value={tauDup} onChange={e => { setTauDup(Number(e.target.value)); markDirty() }} min={0} max={1} step={0.01} className={inputCls} /></div>
            <div><label className={labelCls}>tau_cls（聚类阈值）</label><input type="number" value={tauCls} onChange={e => { setTauCls(Number(e.target.value)); markDirty() }} min={0} max={1} step={0.01} className={inputCls} /></div>
            <div className="col-span-2"><label className={labelCls}>work_dir（工作根目录）</label>
              <input type="text" value={workDir} onChange={e => { setWorkDir(e.target.value); markDirty() }} className={inputCls} />
              <p className="text-xs text-gray-400 dark:text-gray-500 mt-1">
                支持绝对路径或 <code className="bg-gray-100 dark:bg-gray-700 px-1 rounded">{PROJECT_PATH_MACRO}</code> 宏（指向 <code className="bg-gray-100 dark:bg-gray-700 px-1 rounded">{PROJECT_ROOT}</code>）。如 <code className="bg-gray-100 dark:bg-gray-700 px-1 rounded">{PROJECT_PATH_MACRO}/work_dir</code> 表示 <code className="bg-gray-100 dark:bg-gray-700 px-1 rounded">{PROJECT_ROOT}/work_dir</code>。
              </p>
            </div>
            <div className="col-span-2"><label className="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300"><input type="checkbox" checked={recDup} onChange={e => { setRecDup(e.target.checked); markDirty() }} className="rounded" /> record_stage1_duplicates（记录近重复对到侧车）</label></div>
          </div>
        </section>

        {/* ═══ Questions 管理 ═══ */}
        <section className={sectionCls}>
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-medium text-gray-700 dark:text-gray-300">Questions 管理</h3>
            <button onClick={addQuestion} className="px-3 py-1.5 text-xs bg-blue-600 text-white rounded hover:bg-blue-700">+ 添加问题</button>
          </div>
          <p className="text-xs text-gray-400 dark:text-gray-500 mb-3">每个问题定义一条 VLM 标注结构中需要生成的字段。可按模版填写或自由输入 JSON。</p>

          <input
            type="text"
            value={questionSearch}
            onChange={e => setQuestionSearch(e.target.value)}
            placeholder="搜索问题 key 或 description..."
            className="w-full border rounded px-3 py-2 text-sm font-mono mb-3 dark:bg-gray-800 dark:border-gray-600 dark:text-gray-200"
          />

          {questions.length === 0 && <p className="text-xs text-gray-400 py-4 text-center">暂无问题。点击「+ 添加问题」添加。</p>}

          <div className="space-y-3 max-h-[26rem] overflow-y-auto pr-1">
            {questions
              .filter(q => !questionSearch || q.key.toLowerCase().includes(questionSearch.toLowerCase()) || q.description.toLowerCase().includes(questionSearch.toLowerCase()))
              .map((q, idx) => (
              <div key={idx} className={`border rounded-lg p-3 ${q.enabled ? 'border-gray-200 dark:border-gray-600 bg-gray-50 dark:bg-gray-700/50' : 'border-gray-200 dark:border-gray-700 bg-gray-100/50 dark:bg-gray-800/30 opacity-60'}`}>
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center gap-2 flex-1">
                    <span className="text-xs font-medium text-gray-500 dark:text-gray-400">#{idx + 1}</span>
                    <input type="text" value={q.key} onChange={e => updateQuestion(idx, { key: e.target.value })} placeholder="问题 key（如 scene）" className="flex-1 border rounded px-2 py-1 text-sm font-mono dark:bg-gray-800 dark:border-gray-600 dark:text-gray-200" />
                  </div>
                  <div className="flex items-center gap-2">
                    {q.enabled && (() => {
                      function templateFieldsValid(): boolean {
                        if (!q.description || !q.type) return false
                        const validTypes = ['string', 'category', 'int', 'float']
                        if (!validTypes.includes(q.type)) return false
                        if (q.type === 'category' && !q.choices.trim()) return false
                        return true
                      }
                      const canValidate = q._mode === 'template'
                        ? templateFieldsValid()
                        : isFreeformValidatable(q.freeformJson)
                      return (
                        <span className={`px-1.5 py-0.5 text-[10px] rounded-full ${
                          canValidate
                            ? 'bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-300'
                            : 'bg-yellow-100 dark:bg-yellow-900/30 text-yellow-700 dark:text-yellow-300'
                        }`} title={canValidate ? 'VLM 返回结果可被形式校验（类型、取值范围）' : '自由形式 JSON 缺少 description 或 type，结果无法被形式校验'}>
                          {canValidate ? '可校验' : '不可校验'}
                        </span>
                      )
                    })()}
                    <button onClick={() => toggleQuestionEnabled(idx)} className={`px-2 py-0.5 text-xs rounded ${q.enabled ? 'bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-300' : 'bg-gray-200 dark:bg-gray-600 text-gray-500 dark:text-gray-400'}`}>{q.enabled ? '启用' : '暂停'}</button>
                    <button onClick={() => removeQuestion(idx)} className="px-2 py-0.5 text-xs text-red-600 border border-red-200 dark:border-red-800 rounded hover:bg-red-50 dark:hover:bg-red-900/30 dark:text-red-400">删除</button>
                  </div>
                </div>
                {q.enabled && (<>
                <div className="flex items-center gap-3 mb-2">
                  <label className="flex items-center gap-1 text-xs text-gray-500 dark:text-gray-400">
                    <input type="radio" checked={q._mode === 'template'} onChange={() => {
                      if (q._mode === 'freeform' && !isFreeformValidatable(q.freeformJson)) {
                        // 不可解释为模版 → 清空所有模版字段
                        updateQuestion(idx, { _mode: 'template', type: 'string', description: '', choices: '', min: '', max: '', step: '' })
                      } else {
                        updateQuestion(idx, { _mode: 'template' })
                      }
                    }} /> 按模版填写
                  </label>
                  <label className="flex items-center gap-1 text-xs text-gray-500 dark:text-gray-400">
                    <input type="radio" checked={q._mode === 'freeform'} onChange={() => updateQuestion(idx, { _mode: 'freeform' })} /> 自由形式
                  </label>
                </div>
                {q._mode === 'template' ? (
                  <div className="grid grid-cols-2 gap-3">
                    <div className="col-span-2"><label className={labelCls}>description</label><input type="text" value={q.description} onChange={e => updateQuestion(idx, { description: e.target.value })} placeholder="Describe this field" className={inputCls} /></div>
                    <div><label className={labelCls}>type</label><select value={q.type} onChange={e => updateQuestion(idx, { type: e.target.value })} className={inputCls}>
                      <option value="string">string</option><option value="category">category</option><option value="int">int</option><option value="float">float</option>
                    </select></div>
                    {q.type === 'category' && <div className="col-span-1"><label className={labelCls}>choices（每行一个）</label><textarea value={q.choices} onChange={e => updateQuestion(idx, { choices: e.target.value })} rows={3} className={inputCls} /></div>}
                    {(q.type === 'int' || q.type === 'float') && <><div><label className={labelCls}>min</label><input type="number" value={q.min} onChange={e => updateQuestion(idx, { min: e.target.value })} className={inputCls} /></div><div><label className={labelCls}>max</label><input type="number" value={q.max} onChange={e => updateQuestion(idx, { max: e.target.value })} className={inputCls} /></div></>}
                    {q.type === 'float' && <div><label className={labelCls}>step</label><input type="number" value={q.step} onChange={e => updateQuestion(idx, { step: e.target.value })} step={0.1} className={inputCls} /></div>}
                  </div>
                ) : (
                  <div><label className={labelCls}>JSON 定义</label><textarea value={q.freeformJson} onChange={e => updateQuestion(idx, { freeformJson: e.target.value })} rows={4} className={inputCls} /></div>
                )}
                </>)}
              </div>
            ))}
          </div>
        </section>

        {/* ═══ 保存 / 刷新 ═══ */}
        <section className={sectionCls}>
          <div className="flex items-center gap-3 justify-between">
            <p className="text-xs text-gray-400 dark:text-gray-500">「刷新」从后端加载最新配置，「保存」将当前设置写入后端。</p>
            <div className="flex gap-2">
              <button onClick={async () => {
                if (isDirty && !window.confirm('当前有未保存的改动，刷新将丢失这些改动。确定要继续吗？')) return
                await loadEverything()
                setIsDirty(false)
              }} className="px-4 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded hover:bg-gray-50 dark:hover:bg-gray-700 dark:text-gray-300">刷新</button>
              <button onClick={saveAll} className="px-6 py-2 text-sm bg-blue-600 text-white rounded hover:bg-blue-700">保存</button>
            </div>
          </div>
        </section>
      </div>
    </div>
  )
}