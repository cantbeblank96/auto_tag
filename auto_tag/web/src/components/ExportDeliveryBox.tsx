import { useCallback, useEffect, useState } from 'react'
import { api, type ExportDelivery, type ValidateExportDirResult } from '../api/client'

const STORAGE_DELIVERY = 'auto-tag-export-delivery'
const STORAGE_DIR = 'auto-tag-export-local-dir'
const STORAGE_VALIDATED = 'auto-tag-export-dir-validated'

export interface ExportDeliveryConfig {
  delivery: ExportDelivery
  localDir: string
  validated: boolean
  validatedPath: string | null
}

const inputCls =
  'border border-gray-300 dark:border-gray-600 rounded px-2 py-1 text-sm bg-white dark:bg-gray-900 dark:text-gray-200 w-full font-mono'

function loadInitialConfig(): ExportDeliveryConfig {
  try {
    const delivery = (localStorage.getItem(STORAGE_DELIVERY) as ExportDelivery) || 'browser'
    const localDir = localStorage.getItem(STORAGE_DIR) || ''
    const validatedPath = localStorage.getItem(STORAGE_VALIDATED)
    return {
      delivery: delivery === 'local' ? 'local' : 'browser',
      localDir,
      validated: Boolean(validatedPath && localDir),
      validatedPath: validatedPath || null,
    }
  } catch {
    return { delivery: 'browser', localDir: '', validated: false, validatedPath: null }
  }
}

export default function ExportDeliveryBox({
  onChange,
}: {
  onChange: (config: ExportDeliveryConfig) => void
}) {
  const [delivery, setDelivery] = useState<ExportDelivery>(() => loadInitialConfig().delivery)
  const [localDir, setLocalDir] = useState(() => loadInitialConfig().localDir)
  const [validated, setValidated] = useState(() => loadInitialConfig().validated)
  const [validatedPath, setValidatedPath] = useState<string | null>(
    () => loadInitialConfig().validatedPath,
  )
  const [createIfMissing, setCreateIfMissing] = useState(false)
  const [validating, setValidating] = useState(false)
  const [lastValidation, setLastValidation] = useState<ValidateExportDirResult | null>(null)

  const emit = useCallback(
    (patch: Partial<ExportDeliveryConfig>) => {
      const next: ExportDeliveryConfig = {
        delivery: patch.delivery ?? delivery,
        localDir: patch.localDir ?? localDir,
        validated: patch.validated ?? validated,
        validatedPath: patch.validatedPath !== undefined ? patch.validatedPath : validatedPath,
      }
      onChange(next)
    },
    [delivery, localDir, validated, validatedPath, onChange],
  )

  useEffect(() => {
    emit({})
  }, [delivery, localDir, validated, validatedPath, emit])

  const persist = (d: ExportDelivery, dir: string, vPath: string | null) => {
    try {
      localStorage.setItem(STORAGE_DELIVERY, d)
      localStorage.setItem(STORAGE_DIR, dir)
      if (vPath) localStorage.setItem(STORAGE_VALIDATED, vPath)
      else localStorage.removeItem(STORAGE_VALIDATED)
    } catch { /* ignore */ }
  }

  const invalidate = () => {
    setValidated(false)
    setValidatedPath(null)
    setLastValidation(null)
    try {
      localStorage.removeItem(STORAGE_VALIDATED)
    } catch { /* ignore */ }
  }

  const handleDeliveryChange = (d: ExportDelivery) => {
    setDelivery(d)
    persist(d, localDir, d === 'local' && validated ? validatedPath : null)
  }

  const handleDirChange = (v: string) => {
    setLocalDir(v)
    invalidate()
    persist(delivery, v, null)
  }

  const handleValidate = async () => {
    if (!localDir.trim()) {
      setLastValidation({
        ok: false,
        input_path: '',
        path: null,
        exists: false,
        is_directory: false,
        writable: false,
        probe_write_ok: false,
        created: false,
        checks: [{ name: '非空', passed: false, message: '请填写目录路径' }],
        message: '请填写目录路径',
      })
      return
    }
    setValidating(true)
    try {
      const res = await api.validateExportDir(localDir.trim(), createIfMissing)
      setLastValidation(res)
      if (res.ok && res.path) {
        setValidated(true)
        setValidatedPath(res.path)
        setLocalDir(res.path)
        persist('local', res.path, res.path)
      } else {
        invalidate()
      }
    } catch (e: any) {
      setLastValidation({
        ok: false,
        input_path: localDir,
        path: null,
        exists: false,
        is_directory: false,
        writable: false,
        probe_write_ok: false,
        created: false,
        checks: [{ name: '请求', passed: false, message: e.message }],
        message: e.message,
      })
      invalidate()
    } finally {
      setValidating(false)
    }
  }

  return (
    <>
      <p className="text-xs leading-relaxed text-gray-500 dark:text-gray-400">
        「浏览器下载」由浏览器保存附件。「保存到本机目录」由<strong className="font-medium text-gray-700 dark:text-gray-300">运行 FastAPI 的后端进程</strong>直接写入该机器上的文件夹（与浏览器是否远程访问无关）。
      </p>

      <div className="mt-3 flex flex-col gap-2 sm:flex-row sm:gap-6">
        <label className="flex cursor-pointer items-center gap-2 text-sm text-gray-700 dark:text-gray-300">
          <input
            type="radio"
            name="export-delivery"
            checked={delivery === 'browser'}
            onChange={() => handleDeliveryChange('browser')}
          />
          浏览器下载
        </label>
        <label className="flex cursor-pointer items-center gap-2 text-sm text-gray-700 dark:text-gray-300">
          <input
            type="radio"
            name="export-delivery"
            checked={delivery === 'local'}
            onChange={() => handleDeliveryChange('local')}
          />
          保存到本机目录
        </label>
      </div>

      {delivery === 'local' && (
        <div className="mt-4 space-y-3 border-t border-gray-200 pt-4 dark:border-gray-600">
          <label className="block text-xs text-gray-500 dark:text-gray-400">
            目标文件夹（绝对路径或 ~/…）
            <input
              type="text"
              value={localDir}
              onChange={e => handleDirChange(e.target.value)}
              placeholder="/home/user/exports/auto_tag"
              className={`${inputCls} mt-1`}
            />
          </label>
          <label className="flex items-center gap-2 text-xs text-gray-600 dark:text-gray-300">
            <input
              type="checkbox"
              checked={createIfMissing}
              onChange={e => {
                setCreateIfMissing(e.target.checked)
                invalidate()
              }}
            />
            目录不存在时尝试创建
          </label>
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => void handleValidate()}
              disabled={validating}
              className="rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
            >
              {validating ? '验证中…' : '验证路径'}
            </button>
            {validated && validatedPath && (
              <span className="text-xs font-medium text-emerald-600 dark:text-emerald-400">
                ✓ 已验证：{validatedPath}
              </span>
            )}
          </div>
          {lastValidation && (
            <div
              className={`rounded border px-3 py-2 text-xs ${
                lastValidation.ok
                  ? 'border-emerald-300 bg-emerald-50 text-emerald-800 dark:border-emerald-800 dark:bg-emerald-950/30 dark:text-emerald-300'
                  : 'border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-300'
              }`}
            >
              <p className="font-medium">{lastValidation.message}</p>
              <ul className="mt-2 space-y-1">
                {lastValidation.checks.map((c, i) => (
                  <li key={i} className={c.passed ? 'text-emerald-700 dark:text-emerald-400' : ''}>
                    {c.passed ? '✓' : '○'} {c.name}：{c.message}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {delivery === 'local' && !validated && (
            <p className="text-xs text-amber-600 dark:text-amber-400">
              导出前请先点击「验证路径」；未验证时无法写入本机目录。
            </p>
          )}
        </div>
      )}
    </>
  )
}
