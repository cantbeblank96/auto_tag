/** 与设置页、数据库 stats 比对共用的 config 路径宏。 */
export const PROJECT_ROOT = '/home/SENSETIME/xukaiming/Desktop/my_repos/python_projects/kevin_auto_tag/auto_tag'
export const PROJECT_PATH_MACRO = '{PROJECT_PATH}'
export const DEFAULT_CONFIG_PATH = `${PROJECT_PATH_MACRO}/config.json`

export function fromMacroPath(macro: string): string {
  return macro.replace(PROJECT_PATH_MACRO, PROJECT_ROOT)
}
