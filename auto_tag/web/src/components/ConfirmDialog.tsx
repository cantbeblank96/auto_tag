import { useId } from 'react'

/** 统一风格的确认弹窗：与数据库页「高级」删除弹窗一致，替代浏览器原生 confirm。 */
export default function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel = '确定',
  cancelLabel = '取消',
  danger = false,
  busy = false,
  onConfirm,
  onCancel,
}: {
  open: boolean
  title: string
  /** 按 \n 拆分为多段展示；也支持自定义 JSX 内容 */
  message: React.ReactNode
  confirmLabel?: string
  cancelLabel?: string
  /** true 时确认按钮为红色危险样式 */
  danger?: boolean
  busy?: boolean
  onConfirm: () => void
  onCancel: () => void
}) {
  const titleId = useId()
  if (!open) return null
  const paragraphs =
    typeof message === 'string'
      ? message.split('\n').filter(s => s.trim() !== '')
      : null
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/45 p-4">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="w-full max-w-md rounded-xl border border-gray-200 bg-white p-5 shadow-xl dark:border-gray-600 dark:bg-gray-800"
      >
        <h3 id={titleId} className="text-base font-semibold text-gray-900 dark:text-gray-100">
          {title}
        </h3>
        <div className="mt-3 space-y-2 text-sm leading-relaxed text-gray-600 dark:text-gray-300">
          {paragraphs ? paragraphs.map((p, i) => <p key={i}>{p}</p>) : message}
        </div>
        <div className="mt-5 flex justify-end gap-2">
          <button
            type="button"
            disabled={busy}
            onClick={onCancel}
            className="rounded-lg border border-gray-300 px-4 py-2 text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-50 dark:border-gray-600 dark:text-gray-300 dark:hover:bg-gray-700"
          >
            {cancelLabel}
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={onConfirm}
            className={`rounded-lg px-4 py-2 text-sm font-medium text-white disabled:opacity-50 ${
              danger ? 'bg-red-600 hover:bg-red-700' : 'bg-blue-600 hover:bg-blue-700'
            }`}
          >
            {busy ? '处理中…' : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}
