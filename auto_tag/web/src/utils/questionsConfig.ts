export interface QuestionEntry {
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
  tools: string[]
  /** 参考样图：档位值 → 样图路径（模版模式下的编辑态） */
  examples: { value: string; path: string }[]
}

type QuestionDetail = Record<string, any>

export function questionToDetail(q: QuestionEntry): QuestionDetail | null {
  if (!q.enabled) return null
  if (q._mode === 'freeform') {
    try {
      const obj = JSON.parse(q.freeformJson)
      if (Array.isArray(q.tools) && q.tools.length) {
        const existing = Array.isArray(obj.tools) ? obj.tools : []
        obj.tools = Array.from(new Set([...existing, ...q.tools]))
      }
      return obj
    } catch { return null }
  }
  // 保留自由 JSON 中的扩展字段，模版字段则以当前表单为准。
  let original: QuestionDetail = {}
  try {
    const parsed = JSON.parse(q.freeformJson)
    if (isObject(parsed)) original = parsed
  } catch { /* 新建问题或尚未完成的自由 JSON：使用模版字段 */ }
  const d: QuestionDetail = { ...original, description: q.description, type: q.type }
  for (const key of ['choices', 'min', 'max', 'step', 'tools', 'examples']) delete d[key]
  if (q.type === 'category' && q.choices.trim()) {
    d.choices = q.choices.split('\n').map(s => s.trim()).filter(Boolean)
  }
  if (q.type === 'int' || q.type === 'float') {
    if (q.min) d.min = Number(q.min)
    if (q.max) d.max = q.max ? Number(q.max) : null
    if (q.type === 'float' && q.step) d.step = Number(q.step)
  }
  if (Array.isArray(q.tools) && q.tools.length) d.tools = q.tools
  const ex = examplesToObject(q.examples || [])
  if (Object.keys(ex).length) d.examples = ex
  return d
}

export function questionsToObject(
  entries: QuestionEntry[],
  enabledTools?: Set<string>,
): Record<string, any> {
  const questionsObj: Record<string, any> = Object.create(null)
  for (const q of entries) {
    if (!q.key.trim()) continue
    const detail = questionToDetail(q)
    if (!detail) continue
    // 全局关闭的工具：从所有问题绑定中移除
    if (enabledTools && Array.isArray(detail.tools)) {
      detail.tools = detail.tools.filter((t: any) => enabledTools.has(String(t)))
      if (!detail.tools.length) delete detail.tools
    }
    questionsObj[q.key.trim()] = detail
  }
  return questionsObj
}

export function detailToQuestion(key: string, detail: QuestionDetail): QuestionEntry {
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
    tools: Array.isArray(detail.tools) ? detail.tools.map(String) : [],
    examples: examplesFromConfig(detail.examples),
  }
}

export function emptyQuestion(): QuestionEntry {
  return { key: '', enabled: true, _mode: 'template', description: '', type: 'string', choices: '', min: '', max: '', step: '', freeformJson: '{}', tools: [], examples: [] }
}

/** 配置里的 examples 还原成行。一个档位一行；若曾存成路径列表，只保留最后一条。 */
export function examplesFromConfig(raw: unknown): { value: string; path: string }[] {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return []
  const rows: { value: string; path: string }[] = []
  for (const [value, path] of Object.entries(raw as Record<string, unknown>)) {
    let text = ''
    if (typeof path === 'string') text = path.trim()
    else if (Array.isArray(path)) {
      for (const item of path) {
        const next = String(item ?? '').trim()
        if (next) text = next
      }
    }
    if (text) rows.push({ value, path: text })
  }
  return rows
}

/** 模版态 examples 行 → config。同一档位只保留最后一行。 */
export function examplesToObject(rows: { value: string; path: string }[]): Record<string, string> {
  const ex: Record<string, string> = {}
  for (const e of rows || []) {
    const v = e.value.trim()
    const p = e.path.trim()
    if (v && p) ex[v] = p
  }
  return ex
}

/** 将 examples 对象合并进 freeform JSON 文本（空则移除该键）。 */
export function mergeExamplesIntoFreeform(freeformJson: string, rows: { value: string; path: string }[]): string {
  let obj: Record<string, any> = {}
  try { obj = JSON.parse(freeformJson) } catch { /* JSON 不合法时重建 */ }
  const ex = examplesToObject(rows)
  if (Object.keys(ex).length) obj.examples = ex
  else delete obj.examples
  return JSON.stringify(obj, null, 2)
}

export interface QuestionsDocument {
  format: 'auto_tag.questions'
  version: 1
  questions: Record<string, QuestionDetail>
  editor: Record<string, { enabled: boolean; mode: QuestionEntry['_mode'] }>
}

function isObject(value: unknown): value is Record<string, any> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

export function stableStringify(value: unknown): string {
  return JSON.stringify(value, (_, item) => isObject(item)
    ? Object.fromEntries(Object.entries(item).sort(([a], [b]) => a.localeCompare(b)))
    : item)
}

function validateDefinition(key: string, detail: unknown): asserts detail is QuestionDetail {
  if (!isObject(detail)) throw new Error(`问题“${key}”的定义必须是 JSON 对象`)
  if (detail.description != null && typeof detail.description !== 'string') {
    throw new Error(`问题“${key}”的 description 必须是文本`)
  }
  if (detail.type != null && typeof detail.type !== 'string') {
    throw new Error(`问题“${key}”的 type 必须是文本`)
  }
  for (const field of ['choices', 'tools']) {
    if (detail[field] != null && (!Array.isArray(detail[field]) || detail[field].some((v: unknown) => typeof v !== 'string'))) {
      throw new Error(`问题“${key}”的 ${field} 必须是文本列表`)
    }
  }
  for (const field of ['min', 'max', 'step']) {
    if (detail[field] != null && (typeof detail[field] !== 'number' || !Number.isFinite(detail[field]))) {
      throw new Error(`问题“${key}”的 ${field} 必须是有限数值`)
    }
  }
  if (detail.examples != null && (!isObject(detail.examples) || Object.values(detail.examples).some(
    path => typeof path !== 'string' && !(Array.isArray(path) && path.every(p => typeof p === 'string')),
  ))) throw new Error(`问题“${key}”的 examples 必须是档位到图片路径的映射`)
}

export function createQuestionsDocument(entries: QuestionEntry[]): QuestionsDocument {
  const questions: Record<string, QuestionDetail> = Object.create(null)
  const editor: QuestionsDocument['editor'] = Object.create(null)
  for (const [index, question] of entries.entries()) {
    const key = question.key.trim()
    if (!key) throw new Error(`第 ${index + 1} 个问题缺少 key，请填写后再导出或导入`)
    if (Object.hasOwn(questions, key)) throw new Error(`问题 key 重复：${key}`)
    let detail: unknown
    if (question._mode === 'freeform') {
      try { detail = JSON.parse(question.freeformJson) }
      catch { throw new Error(`问题“${key}”的自由 JSON 无法解析，请修正后重试`) }
      validateDefinition(key, detail)
    }
    detail = questionToDetail({ ...question, enabled: true })
    validateDefinition(key, detail)
    questions[key] = detail
    editor[key] = { enabled: question.enabled, mode: question._mode }
  }
  return { format: 'auto_tag.questions', version: 1, questions, editor }
}

export function parseQuestionsDocument(text: string): QuestionEntry[] {
  let document: unknown
  try { document = JSON.parse(text.replace(/^\uFEFF/, '')) }
  catch { throw new Error('配置文件不是有效的 JSON，请检查文件内容') }
  if (!isObject(document) || document.format !== 'auto_tag.questions') {
    throw new Error('请选择 Questions 管理导出的配置文件（format: auto_tag.questions）')
  }
  if (document.version !== 1) throw new Error(`不支持 Questions 配置版本：${document.version}`)
  if (!isObject(document.questions) || !isObject(document.editor)) {
    throw new Error('配置文件中的 questions 和 editor 必须是对象')
  }
  const entries: QuestionEntry[] = []
  const keys = new Set<string>()
  for (const [rawKey, detail] of Object.entries(document.questions)) {
    const key = rawKey.trim()
    if (!key || keys.has(key)) throw new Error(`问题 key 为空或重复：${rawKey}`)
    keys.add(key)
    validateDefinition(key, detail)
    const state = document.editor[rawKey]
    if (!isObject(state) || typeof state.enabled !== 'boolean' || !['template', 'freeform'].includes(state.mode)) {
      throw new Error(`问题“${key}”缺少有效的 enabled 或 mode 编辑状态`)
    }
    const entry: QuestionEntry = { ...detailToQuestion(key, detail), enabled: state.enabled, _mode: state.mode }
    if (state.mode === 'template' && stableStringify(questionToDetail({ ...entry, enabled: true })) !== stableStringify(detail)) {
      throw new Error(`问题“${key}”无法完整转换为模版，请将 editor 中的 mode 改为 freeform（自由 JSON）后重试`)
    }
    entries.push(entry)
  }
  if (Object.keys(document.editor).some(key => !Object.hasOwn(document.questions, key))) {
    throw new Error('editor 中存在没有对应问题定义的条目')
  }
  return entries
}

export function questionsFromConfig(config: Record<string, any>): QuestionEntry[] {
  if (config.questions_editor != null) {
    return parseQuestionsDocument(JSON.stringify(config.questions_editor))
  }
  return Object.entries(config.questions || {}).map(([key, detail]) => detailToQuestion(key, detail as QuestionDetail))
}

export interface SavedQuestions {
  document: QuestionsDocument
  questions: Record<string, any>
}

export class QuestionsImportSavedError extends Error {
  saved: SavedQuestions
  constructor(saved: SavedQuestions, reason: string) {
    super(`Questions 已保存，但热加载失败，新配置未生效：${reason}。请重试导入或重启后端。`)
    this.saved = saved
  }
}

async function configRequest(url: string, init?: RequestInit): Promise<any> {
  const response = await fetch(url, init)
  const body = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : `HTTP ${response.status}`)
  return body
}

export async function saveImportedQuestions(path: string, entries: QuestionEntry[]): Promise<SavedQuestions> {
  // 所有校验先于任何请求，避免部分写盘。
  const document = createQuestionsDocument(entries)
  const data = await configRequest(`/api/utils/read_file?path=${encodeURIComponent(path)}`)
  const config = typeof data.content === 'string' ? JSON.parse(data.content) : data.content
  if (!isObject(config)) throw new Error('后端配置不是 JSON 对象，无法合并 Questions')
  const enabledTools = new Set(
    Object.entries(config.annotation_tools || {})
      .filter(([key, value]) => key !== 'align_model_paths' && isObject(value) && value.enabled === true)
      .map(([key]) => key),
  )
  const questions = questionsToObject(entries, enabledTools)
  const saved = { document, questions }
  await configRequest('/api/utils/write_file', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      path, content: JSON.stringify({ ...config, questions, questions_editor: document }, null, 4),
    }),
  })
  try {
    await configRequest('/api/utils/reload_config', { method: 'POST' })
  } catch (error) {
    throw new QuestionsImportSavedError(saved, error instanceof Error ? error.message : String(error))
  }
  return saved
}

