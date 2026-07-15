/** 与设置页、数据库 stats 比对共用的 config 路径宏。 */
export const PROJECT_PATH_MACRO = '{PROJECT_PATH}'
export const DEFAULT_CONFIG_PATH = `${PROJECT_PATH_MACRO}/config.json`

let _projectRoot: string | null = null
let _projectRootPromise: Promise<string> | null = null

/** 当前已缓存的 auto_tag 包绝对路径（拉取前为空字符串）。 */
export function getProjectRoot(): string {
  return _projectRoot ?? ''
}

/**
 * 从后端获取本机 auto_tag 包路径并缓存。
 * 勿在前端硬编码开发机路径，否则跨机器部署时 read_file 会 403。
 */
export async function ensureProjectRoot(): Promise<string> {
  if (_projectRoot) return _projectRoot
  if (!_projectRootPromise) {
    _projectRootPromise = (async () => {
      const res = await fetch('/api/utils/paths')
      if (!res.ok) {
        throw new Error(`Failed to resolve project path: HTTP ${res.status}`)
      }
      const data = await res.json()
      const root = String(data.project_path || '').trim()
      if (!root) {
        throw new Error('Failed to resolve project path: empty project_path')
      }
      _projectRoot = root
      return root
    })()
  }
  return _projectRootPromise
}

/** 将宏路径替换为绝对路径；调用前须先 ensureProjectRoot / resolveMacroPath。 */
export function fromMacroPath(macro: string): string {
  const root = _projectRoot
  if (!root) return macro
  return macro.replace(PROJECT_PATH_MACRO, root)
}

/** 确保已解析 PROJECT_ROOT 后替换宏。 */
export async function resolveMacroPath(macro: string): Promise<string> {
  await ensureProjectRoot()
  return fromMacroPath(macro)
}
