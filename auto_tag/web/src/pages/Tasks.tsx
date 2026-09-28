import { useState, useEffect, useRef, type ReactNode } from 'react'
import { api, type JobStatusResponse, type JobSummary } from '../api/client'
import TaskQuerySection from '../components/TaskQuerySection'
import ConfirmDialog from '../components/ConfirmDialog'
import { formatJobRuntime } from '../utils/jobDuration'

const sectionTitleCls =
  'text-lg font-semibold text-gray-800 dark:text-gray-100 border-b border-gray-200 dark:border-gray-700 pb-2 mb-4'

function ChapterSection({
  title,
  children,
  defaultCollapsed = false,
}: {
  title: string
  children: ReactNode
  defaultCollapsed?: boolean
}) {
  const [open, setOpen] = useState(!defaultCollapsed)
  return (
    <section className="mb-10 last:mb-0">
      <h3
        className={`${sectionTitleCls} cursor-pointer select-none flex items-center gap-2`}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="text-xs text-blue-500 shrink-0">{open ? '▼' : '▶'}</span>
        {title}
      </h3>
      {open && children}
    </section>
  )
}

const ROTATE_OPTIONS = [
  { label: '不旋转', value: '' },
  { label: '顺时针 90° (ROTATE_90_CLOCKWISE)', value: 'ROTATE_90_CLOCKWISE' },
  { label: '180° (ROTATE_180)', value: 'ROTATE_180' },
  { label: '逆时针 90° (ROTATE_90_COUNTERCLOCKWISE)', value: 'ROTATE_90_COUNTERCLOCKWISE' },
]

type MediaMode = 'yuv' | 'image' | 'mixed'

const YUV_FILE_SUFFIXES = ['.nv21', '.nv12', '.yuv']
const IMAGE_FILE_SUFFIXES = ['.jpg', '.jpeg', '.png', '.webp', '.bmp']

const MEDIA_MODE_OPTIONS: { id: MediaMode; label: string; hint: string }[] = [
  { id: 'yuv', label: '标注原始 YUV', hint: '只收 .nv21 / .nv12 / .yuv，不收可视化图片' },
  { id: 'image', label: '标注可视化图片', hint: '只收 .jpg / .jpeg / .png / .webp / .bmp' },
  { id: 'mixed', label: '混合目录', hint: 'YUV 与图片都作为独立文件参与标注' },
]

function suffixesForMedia(mode: MediaMode): string[] {
  if (mode === 'yuv') return YUV_FILE_SUFFIXES
  if (mode === 'image') return IMAGE_FILE_SUFFIXES
  return [...IMAGE_FILE_SUFFIXES, ...YUV_FILE_SUFFIXES]
}

function mediaNeedsYuv(mode: MediaMode): boolean {
  return mode !== 'image'
}

function inferMediaMode(data: Record<string, any>): MediaMode {
  const explicit = data.media_mode
  if (explicit === 'yuv' || explicit === 'image' || explicit === 'mixed') return explicit
  const suffixes = (Array.isArray(data.image_suffixes) ? data.image_suffixes : []).map((s: unknown) => {
    const text = String(s || '').trim().toLowerCase()
    if (!text) return ''
    return text.startsWith('.') ? text : `.${text}`
  }).filter(Boolean)
  const yuvSet = new Set(YUV_FILE_SUFFIXES)
  const imageSet = new Set(IMAGE_FILE_SUFFIXES)
  const hasYuv = suffixes.some(s => yuvSet.has(s))
  const hasImage = suffixes.some(s => imageSet.has(s))
  if (hasYuv && !hasImage) return 'yuv'
  if (hasImage && !hasYuv) return 'image'
  if (data.b_yuv_image && !data.mixed_yuv) return 'yuv'
  if (data.mixed_yuv) return 'mixed'
  return 'image'
}

interface QueueItem {
  queueId: string
  summary: string
  inputDirs: string[]
  /** 图片来源模式：目录扫描 / 列表指定 */
  sourceMode: 'dir' | 'list'
  imageLsFiles: string[]
  /** 目录扫描收哪些文件；列表模式不按此过滤 */
  mediaMode: MediaMode
  rotateAngle: string
  mixedYuv: boolean
  bYuv: boolean
  yuvW: number
  yuvH: number
  yuvType: string
  status: 'queued' | 'running' | 'completed' | 'failed'
  serverJobId: string | null
  error: string | null
  lastJob?: JobStatusResponse
  /** 创建时间戳（毫秒），来自后端或本地 Date.now() */
  createdAt: number
}

const STATUS_LABEL: Record<string, string> = {
  queued: '排队中',
  running: '运行中',
  completed: '已完成',
  failed: '失败',
}

function fmtRatio(num: number, den: number): string {
  if (den <= 0) return String(num)
  return `${num} (${(num / den * 100).toFixed(0)}%)`
}

/** 把系统目录窗口返回的绝对路径追加到「输入目录」多行文本。已存在则不重复添加。 */
function appendDirectoryPath(current: string, picked: string): { text: string; added: boolean } {
  const path = picked.trim()
  if (!path) return { text: current, added: false }
  const lines = current.split('\n').map(s => s.trim()).filter(Boolean)
  if (lines.includes(path)) return { text: current, added: false }
  if (!current.trim()) return { text: path, added: true }
  return { text: `${current.replace(/\n+$/, '')}\n${path}`, added: true }
}

/** localStorage key */
const LS_LAST_SEEN = 'auto_tag_tasks_last_seen'

export default function Tasks() {
  const [inputDirs, setInputDirs] = useState('')
  const [pickingDir, setPickingDir] = useState(false)
  const [rotLabel, setRotLabel] = useState(ROTATE_OPTIONS[0].label)
  const [yuvW, setYuvW] = useState(640)
  const [yuvH, setYuvH] = useState(480)
  const [yuvType, setYuvType] = useState('nv21')
  const [sourceMode, setSourceMode] = useState<'dir' | 'list'>('dir')
  const [imageLsFilesText, setImageLsFilesText] = useState('')
  const [mediaMode, setMediaMode] = useState<MediaMode>('image')
  const [skipIfInDb, setSkipIfInDb] = useState(true)
  // 本地队列（当前 session 提交的 + 从后端拉取的）
  const [queue, setQueue] = useState<QueueItem[]>([])
  const [queueArmed, setQueueArmed] = useState(false)
  const [msg, setMsg] = useState('')
  const [msgLevel, setMsgLevel] = useState<'info' | 'error'>('info')
  // 时间点过滤
  const [lastSeenAt, setLastSeenAt] = useState<number>(() => {
    const stored = localStorage.getItem(LS_LAST_SEEN)
    return stored ? Number(stored) : 0
  })

  const showMsg = (text: string, level: 'info' | 'error' = 'info') => {
    setMsg(text)
    setMsgLevel(level)
    // 错误提示停留更久，避免用户误以为操作无响应
    setTimeout(() => setMsg(''), level === 'error' ? 15000 : 5000)
  }

  const pickInputDir = async () => {
    if (pickingDir) return
    const lines = inputDirs.split('\n').map(s => s.trim()).filter(Boolean)
    const initial = lines.length ? lines[lines.length - 1] : ''
    setPickingDir(true)
    try {
      const res = await api.pickDirectory(initial)
      if (res.cancelled || !res.path) return
      const next = appendDirectoryPath(inputDirs, res.path)
      if (!next.added) {
        showMsg('该目录已在输入目录中')
        return
      }
      setInputDirs(next.text)
    } catch (e: any) {
      showMsg(e?.message || '无法打开目录选择窗口', 'error')
    } finally {
      setPickingDir(false)
    }
  }

  const skipIfInDbRef = useRef(skipIfInDb)
  skipIfInDbRef.current = skipIfInDb
  const queueRef = useRef<QueueItem[]>([])
  queueRef.current = queue
  /** 防止 Strict Mode / 并发 tick 对同一排队项重复 createJob */
  const createInFlightRef = useRef<string | null>(null)

  // 运行中任务时长每秒刷新
  const [nowSec, setNowSec] = useState(() => Date.now() / 1000)
  useEffect(() => {
    const hasRunning = queue.some(q => q.status === 'running')
    if (!hasRunning) return
    const t = setInterval(() => setNowSec(Date.now() / 1000), 1000)
    return () => clearInterval(t)
  }, [queue])

  // --- 挂载时从后端拉取历史任务 ---
  useEffect(() => {
    api.listJobs().then(resp => {
      const serverStarted = resp.server_started_at
      // 若从未设置过 lastSeenAt，以服务启动时间为默认值
      if (!localStorage.getItem(LS_LAST_SEEN) && serverStarted > 0) {
        const ms = serverStarted * 1000
        setLastSeenAt(ms)
        localStorage.setItem(LS_LAST_SEEN, String(ms))
      }
      // 将后端任务合并到 queue 中（跳过已存在的 serverJobId）
      setQueue(prev => {
        const existingIds = new Set(prev.map(q => q.serverJobId).filter(Boolean))
        const newItems: QueueItem[] = []
        for (const j of resp.jobs) {
          if (j.job_id && !existingIds.has(j.job_id)) {
            newItems.push(jobSummaryToQueueItem(j))
          }
        }
        return [...prev, ...newItems]
      })
    }).catch(() => {})
  }, [])

  function jobSummaryToQueueItem(j: JobSummary): QueueItem {
    const statusMap: Record<string, QueueItem['status']> = {
      queued: 'queued',
      running: 'running',
      done: 'completed',
      failed: 'failed',
    }
    return {
      queueId: `job_${j.job_id.slice(0, 8)}`,
      summary: `${j.job_id.slice(0, 8)} (${j.work_dir || '?'})`,
      inputDirs: [],
      sourceMode: 'dir',
      imageLsFiles: [],
      mediaMode: 'image',
      rotateAngle: '',
      mixedYuv: false,
      bYuv: false,
      yuvW: 640,
      yuvH: 480,
      yuvType: 'nv21',
      status: statusMap[j.status] || 'failed',
      serverJobId: j.job_id,
      error: j.error || null,
      lastJob: j as unknown as JobStatusResponse,
      createdAt: (j.created_at || 0) * 1000,
    }
  }

  // --- 显示过滤：只展示 createdAt > lastSeenAt 的任务 ---
  const visibleQueue = queue.filter(q => q.createdAt >= lastSeenAt)

  // --- 本地新建的任务若无后端的 createdAt，用 Infinity 确保始终可见 ---
  const displayQueue = visibleQueue.map(q =>
    q.createdAt === 0 ? { ...q, createdAt: Infinity } : q
  )

  // --- 清除历史：将 lastSeenAt 更新为当前时间 ---
  const handleClearHistory = () => {
    const now = Date.now()
    setLastSeenAt(now)
    localStorage.setItem(LS_LAST_SEEN, String(now))
    showMsg('已隐藏此时间之前的任务记录（可在下方「管理」章节查看全部）')
  }

  // 轮询运行中任务；空闲时提交下一个排队项（副作用不得放在 setState updater 内）
  useEffect(() => {
    if (!queueArmed) return
    let cancelled = false

    const tick = () => {
      const snapshot = queueRef.current
      const running = snapshot.filter(q => q.status === 'running' && q.serverJobId)

      for (const item of running) {
        const jobId = item.serverJobId!
        api.getJob(jobId).then(job => {
          if (cancelled) return
          setQueue(prev => prev.map(q => {
            if (q.queueId !== item.queueId) return q
            const next: QueueItem = { ...q, lastJob: job }
            if (job.status === 'done') {
              next.status = 'completed'
            } else if (job.status === 'failed') {
              next.status = 'failed'
              next.error = job.error || 'failed'
            }
            return next
          }))
        }).catch(() => { })
      }

      if (running.length > 0 || createInFlightRef.current) return

      const nextItem = snapshot.find(q => q.status === 'queued')
      if (!nextItem) {
        setQueueArmed(false)
        return
      }

      createInFlightRef.current = nextItem.queueId
      api.createJob({
        input_dirs: nextItem.sourceMode === 'dir' ? nextItem.inputDirs : [],
        image_ls_files: nextItem.sourceMode === 'list' ? nextItem.imageLsFiles : [],
        image_suffixes: nextItem.sourceMode === 'dir' ? suffixesForMedia(nextItem.mediaMode) : null,
        image_name_regex: null,
        filter_ignore_case: true,
        filter_match_full_path: false,
        rotate_angle: nextItem.rotateAngle || null,
        b_yuv_image: false,
        mixed_yuv: mediaNeedsYuv(nextItem.mediaMode),
        yuv_type: nextItem.yuvType,
        image_width: mediaNeedsYuv(nextItem.mediaMode) ? nextItem.yuvW : 0,
        image_height: mediaNeedsYuv(nextItem.mediaMode) ? nextItem.yuvH : 0,
        skip_if_in_db: skipIfInDbRef.current,
      }).then(res => {
        if (cancelled) return
        setQueue(prev => prev.map(q =>
          q.queueId === nextItem.queueId
            ? {
                ...q,
                serverJobId: res.job_id,
                status: 'running' as const,
                error: null,
                summary: `${res.job_id.slice(0, 8)} (${q.summary})`,
              }
            : q,
        ))
      }).catch(e => {
        if (cancelled) return
        const msg = String(e?.message || e)
        const busy = /already running|已有任务在运行/i.test(msg)
        if (busy) {
          // 后端忙碌时保持排队，下一轮 tick 再试（不标失败）
          return
        }
        setQueue(prev => prev.map(q =>
          q.queueId === nextItem.queueId
            ? { ...q, status: 'failed' as const, error: msg }
            : q,
        ))
      }).finally(() => {
        if (createInFlightRef.current === nextItem.queueId) {
          createInFlightRef.current = null
        }
      })
    }

    tick()
    const interval = setInterval(tick, 2000)
    return () => {
      cancelled = true
      clearInterval(interval)
    }
  }, [queueArmed])

  const [missingDirs, setMissingDirs] = useState<string[] | null>(null)

  /** 将当前表单状态打包为队列项并入队（confirmTask 校验通过与缺失目录弹窗确认后共用）。 */
  const enqueueCurrentForm = (summary: string) => {
    const dirs = inputDirs.split('\n').map(s => s.trim()).filter(Boolean)
    const lsFiles = imageLsFilesText.split('\n').map(s => s.trim()).filter(Boolean)
    const newItem: QueueItem = {
      queueId: Math.random().toString(36).slice(2, 10),
      summary,
      inputDirs: dirs,
      sourceMode,
      imageLsFiles: lsFiles,
      mediaMode,
      rotateAngle: ROTATE_OPTIONS.find(o => o.label === rotLabel)?.value || '',
      mixedYuv: mediaNeedsYuv(mediaMode),
      bYuv: false,
      yuvW,
      yuvH,
      yuvType,
      status: 'queued',
      serverJobId: null,
      error: null,
      createdAt: Date.now(),
    }
    setQueue(prev => [...prev, newItem])
    showMsg('已加入任务队列，请在「提交任务」中执行')
  }

  const confirmTask = async () => {
    const isList = sourceMode === 'list'
    const dirs = inputDirs.split('\n').map(s => s.trim()).filter(Boolean)
    const lsFiles = imageLsFilesText.split('\n').map(s => s.trim()).filter(Boolean)
    if (mediaNeedsYuv(mediaMode) && (yuvW <= 0 || yuvH <= 0)) {
      showMsg('标注 YUV 需要填写大于 0 的宽度和高度', 'error')
      return
    }
    let summary: string
    if (isList) {
      if (lsFiles.length === 0) {
        showMsg('请至少填写一个 image_ls 文件路径')
        return
      }
      summary = lsFiles.length === 1 ? `[列表] ${lsFiles[0]}` : `[列表] ${lsFiles.length} 个列表文件`
    } else {
      if (dirs.length === 0) {
        showMsg('请至少填写一个输入目录')
        return
      }
      try {
        const check = await api.checkDirs(dirs)
        if (check.not_exist.length > 0) {
          // 存在缺失目录：弹窗确认后再入队
          setMissingDirs(check.not_exist)
          return
        }
      } catch (e: any) {
        showMsg(`无法校验目录（已跳过验证）: ${e.message}`)
      }
      summary = dirs.length === 1 ? dirs[0] : `${dirs[0]} 等${dirs.length}项`
    }
    enqueueCurrentForm(summary)
  }

  const downloadTaskJson = () => {
    const data = {
      version: 1,
      source_mode: sourceMode,
      input_dirs: inputDirs.split('\n').map(s => s.trim()).filter(Boolean),
      image_ls_files: imageLsFilesText.split('\n').map(s => s.trim()).filter(Boolean),
      media_mode: mediaMode,
      image_suffixes: sourceMode === 'dir' ? suffixesForMedia(mediaMode) : null,
      image_name_regex: null,
      filter_ignore_case: true,
      filter_match_full_path: false,
      rotate_angle: ROTATE_OPTIONS.find(o => o.label === rotLabel)?.value || null,
      b_yuv_image: false,
      mixed_yuv: mediaNeedsYuv(mediaMode),
      yuv_type: yuvType,
      image_width: yuvW,
      image_height: yuvH,
      skip_if_in_db: skipIfInDb,
    }
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = 'auto_tag_job.json'
    a.click()
    URL.revokeObjectURL(a.href)
  }

  const uploadTaskJson = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    const reader = new FileReader()
    reader.onload = (ev) => {
      try {
        const data = JSON.parse(ev.target?.result as string)
        if (data.input_dirs) {
          setInputDirs(Array.isArray(data.input_dirs) ? data.input_dirs.join('\n') : data.input_dirs)
        }
        if (Array.isArray(data.image_ls_files)) {
          setImageLsFilesText(data.image_ls_files.join('\n'))
        }
        // 模式推断：显式 source_mode 优先；否则有列表且无目录时切到列表模式
        if (data.source_mode === 'list' || data.source_mode === 'dir') {
          setSourceMode(data.source_mode)
        } else if (
          Array.isArray(data.image_ls_files) && data.image_ls_files.length > 0 &&
          (!Array.isArray(data.input_dirs) || data.input_dirs.length === 0)
        ) {
          setSourceMode('list')
        }
        const hadRegex = Boolean(data.image_name_regex)
        setMediaMode(inferMediaMode(data))
        if (data.rotate_angle) {
          const opt = ROTATE_OPTIONS.find(o => o.value === data.rotate_angle)
          if (opt) setRotLabel(opt.label)
        }
        if (data.yuv_type) setYuvType(data.yuv_type)
        if (data.image_width) setYuvW(data.image_width)
        if (data.image_height) setYuvH(data.image_height)
        if (data.skip_if_in_db != null) setSkipIfInDb(data.skip_if_in_db)
        showMsg(
          hadRegex
            ? '已加载到表单。文件名正则过滤已不再使用，本次按媒体类型收集文件'
            : '已加载到表单',
          hadRegex ? 'error' : 'info',
        )
      } catch (e: any) {
        showMsg(`JSON 解析失败: ${e.message}`)
      }
    }
    reader.readAsText(file)
    e.target.value = ''
  }

  return (
    <div>
      <h2 className="text-2xl font-semibold text-gray-800 dark:text-gray-100 mb-6">任务</h2>
      {msg && (
        <div className={`mb-4 px-4 py-2 rounded text-sm border ${
          msgLevel === 'error'
            ? 'bg-red-50 dark:bg-red-950/50 text-red-700 dark:text-red-300 border-red-200 dark:border-red-800'
            : 'bg-blue-50 dark:bg-blue-950/50 text-blue-700 dark:text-blue-300 border-blue-200 dark:border-blue-800'
        }`}>
          {msg}
        </div>
      )}

      <ChapterSection title="标注">
        <section className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4 mb-6">
          <h4 className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-3">加载 & 保存</h4>
        <div className="flex items-center gap-4">
          <button onClick={downloadTaskJson} className="px-3 py-1.5 text-sm border border-gray-300 rounded hover:bg-gray-50">
            保存并下载任务 JSON
          </button>
          <label className="px-3 py-1.5 text-sm border border-gray-300 rounded hover:bg-gray-50 cursor-pointer">
            上传 JSON 加载到表单
            <input type="file" accept=".json" onChange={uploadTaskJson} className="hidden" />
          </label>
        </div>
        </section>

        <section className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4 mb-6">
          <h4 className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-3">新建</h4>
        <div className="space-y-4">
          {/* F1：图片来源双模式切换 */}
          <div className="flex gap-2">
            {([['dir', '目录扫描'], ['list', '列表指定 (image_ls)']] as const).map(([mode, label]) => (
              <button
                key={mode}
                onClick={() => setSourceMode(mode)}
                className={`px-3 py-1.5 text-sm rounded border ${
                  sourceMode === mode
                    ? 'bg-blue-600 text-white border-blue-600'
                    : 'border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700'
                }`}
              >
                {label}
              </button>
            ))}
          </div>
          {sourceMode === 'dir' ? (
            <>
              <div>
                <div className="flex items-center justify-between gap-3 mb-1">
                  <label className="block text-sm text-gray-600 dark:text-gray-400">输入目录（每行一个绝对路径）</label>
                  <button
                    type="button"
                    onClick={() => void pickInputDir()}
                    disabled={pickingDir}
                    title="在运行后端的电脑上打开系统目录窗口，可选任意磁盘中的文件夹"
                    className="shrink-0 px-3 py-1 text-sm border border-gray-300 dark:border-gray-600 rounded hover:bg-gray-50 dark:hover:bg-gray-700 disabled:opacity-50"
                  >
                    {pickingDir ? '请在弹出的窗口中选择…' : '添加目录'}
                  </button>
                </div>
                <textarea
                  value={inputDirs}
                  onChange={e => setInputDirs(e.target.value)}
                  placeholder={'/path/to/images 或 D:\\images'}
                  className="w-full border rounded px-3 py-2 text-sm font-mono"
                  rows={4}
                />
                <p className="text-xs text-gray-400 mt-1">
                  「添加目录」会在运行后端的电脑上弹出系统目录窗口，选中后把绝对路径追加到上面。Windows 下可选任意盘符（如 D:\images）；也可以直接粘贴路径。
                </p>
              </div>
            </>
          ) : (
            <div>
              <label className="block text-sm text-gray-600 dark:text-gray-400 mb-1">image_ls 文件路径（每行一个绝对路径，可多个）</label>
              <textarea
                value={imageLsFilesText}
                onChange={e => setImageLsFilesText(e.target.value)}
                placeholder="/path/to/image_ls.txt"
                className="w-full border rounded px-3 py-2 text-sm font-mono"
                rows={4}
              />
              <p className="text-xs text-gray-400 mt-1">
                列表文件格式：首行可为 JSON 头部（如 {'{"prefix": "/data/imgs/", "image_num": 3}'}），其余每行一个路径（相对行与 prefix 拼接）；也兼容旧版 JSON 数组格式。列表里有什么就标什么，不按待标注文件类型过滤。
              </p>
            </div>
          )}
          <div className="border border-gray-200 dark:border-gray-700 rounded p-3 space-y-2">
            <p className="text-sm text-gray-600 dark:text-gray-400">待标注文件</p>
            {MEDIA_MODE_OPTIONS.map(opt => (
              <label key={opt.id} className="flex items-start gap-2 text-sm text-gray-700 dark:text-gray-300">
                <input
                  type="radio"
                  name="media-mode"
                  className="mt-0.5"
                  checked={mediaMode === opt.id}
                  onChange={() => setMediaMode(opt.id)}
                />
                <span>
                  <span className="font-medium">{opt.label}</span>
                  <span className="block text-xs text-gray-400">{opt.hint}</span>
                </span>
              </label>
            ))}
            <p className="text-xs text-gray-400">
              {sourceMode === 'dir'
                ? '目录扫描只收集所选类型。后缀匹配忽略大小写，Windows 上的 .JPG、.NV21 同样会收进来。'
                : '列表模式不按这里过滤路径。选中含 YUV 的选项时，列表里的 .nv21 / .nv12 / .yuv 按 YUV 解码，其余按图片解码。'}
            </p>
          </div>
          <div>
            <label className="block text-sm text-gray-600 dark:text-gray-400 mb-1">rotate_angle（可选）</label>
            <select value={rotLabel} onChange={e => setRotLabel(e.target.value)} className="border rounded px-3 py-2 text-sm">
              {ROTATE_OPTIONS.map(o => <option key={o.value} value={o.label}>{o.label}</option>)}
            </select>
          </div>
          {mediaNeedsYuv(mediaMode) && (
            <div>
              <p className="text-sm text-gray-600 dark:text-gray-400 mb-2">YUV 解码</p>
              <p className="text-xs text-gray-400 mb-2">
                .nv21 / .nv12 按后缀解码。.yuv 使用这里选择的格式（文件名里含 nv12、nv21、420p 时优先跟文件名）。
              </p>
              <div className="flex gap-4">
                <div>
                  <label className="block text-xs text-gray-500">宽度</label>
                  <input type="number" min={1} value={yuvW} onChange={e => setYuvW(Number(e.target.value))} className="border rounded px-2 py-1 text-sm w-24" />
                </div>
                <div>
                  <label className="block text-xs text-gray-500">高度</label>
                  <input type="number" min={1} value={yuvH} onChange={e => setYuvH(Number(e.target.value))} className="border rounded px-2 py-1 text-sm w-24" />
                </div>
                <div>
                  <label className="block text-xs text-gray-500">格式</label>
                  <select value={yuvType} onChange={e => setYuvType(e.target.value)} className="border rounded px-2 py-1 text-sm">
                    <option value="nv12">NV12</option>
                    <option value="nv21">NV21</option>
                    <option value="yuv420p">420p</option>
                  </select>
                </div>
              </div>
            </div>
          )}
          <button onClick={confirmTask} className="px-4 py-2 text-sm bg-blue-600 text-white rounded hover:bg-blue-700">
            确认
          </button>
        </div>
        </section>

        <section className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
          <h4 className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-3">运行</h4>
        <div className="mb-4">
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={skipIfInDb} onChange={e => setSkipIfInDb(e.target.checked)} />
            跳过库中已有路径（队列中所有任务统一生效）
          </label>
          <p className="text-xs text-gray-400 mt-1 ml-6">
            勾选后若向量库或近重复侧车中已有相同路径则跳过；不勾选则先删旧记录再重新处理。
          </p>
        </div>

        {/* 清除历史按钮 */}
        <div className="mb-4 flex items-center gap-3">
          <button
            onClick={handleClearHistory}
            className="px-3 py-1.5 text-xs border border-gray-300 rounded hover:bg-gray-50 text-gray-500"
          >
            清除历史记录（隐藏此刻之前的任务）
          </button>
          {lastSeenAt > 0 && (
            <span className="text-xs text-gray-400">
              仅显示 {new Date(lastSeenAt).toLocaleString()} 之后的任务
            </span>
          )}
        </div>

        {displayQueue.length > 0 && (
          <div className="overflow-x-auto mb-4">
            <table className="w-full text-sm border-collapse">
              <thead>
                <tr className="bg-gray-50">
                  <th className="text-left px-3 py-2 text-gray-600 dark:text-gray-400 font-medium">ID</th>
                  <th className="text-left px-3 py-2 text-gray-600 dark:text-gray-400 font-medium">摘要</th>
                  <th className="text-left px-3 py-2 text-gray-600 dark:text-gray-400 font-medium">状态</th>
                  <th className="text-right px-3 py-2 text-gray-600 dark:text-gray-400 font-medium">耗时</th>
                  <th className="text-right px-3 py-2 text-gray-600 dark:text-gray-400 font-medium">已收集</th>
                  <th className="text-right px-3 py-2 text-gray-600 dark:text-gray-400 font-medium">已处理</th>
                  <th className="text-right px-3 py-2 text-gray-600 dark:text-gray-400 font-medium">打标数</th>
                  <th className="text-right px-3 py-2 text-gray-600 dark:text-gray-400 font-medium">跳过数</th>
                  <th className="text-right px-3 py-2 text-gray-600 dark:text-gray-400 font-medium">失败数</th>
                </tr>
              </thead>
              <tbody>
                {displayQueue.map((item) => {
                  const lj = item.lastJob
                  const total = lj?.total || 0
                  const proc = lj?.processed || 0
                  const fail = lj?.failed_so_far || 0
                  const skDb = lj?.skip_in_db || 0
                  const vlm = lj?.vlm_calls || 0
                  const vlmFail = lj?.vlm_failed || 0
                  const vlmTotal = lj?.new_centers || 0
                  const s1 = lj?.stage1_skips || 0
                  const s2 = lj?.stage2_joins || 0
                  const skipAll = skDb + s1 + s2
                  const den = proc || 1
                  const vlmDen = vlmTotal > 0 ? vlmTotal : den
                  return (
                    <tr key={item.queueId} className="border-t border-gray-100">
                      <td className="px-3 py-2 text-xs font-mono text-gray-500">{item.queueId}</td>
                      <td className="px-3 py-2 text-gray-700 dark:text-gray-300 max-w-48 truncate">{item.summary}</td>
                      <td className="px-3 py-2">
                        <span className={`text-xs px-2 py-0.5 rounded ${item.status === 'completed' ? 'bg-green-100 text-green-700' :
                            item.status === 'running' ? 'bg-blue-100 text-blue-700' :
                              item.status === 'failed' ? 'bg-red-100 text-red-700' :
                                'bg-gray-100 text-gray-600 dark:text-gray-400'
                          }`}>{STATUS_LABEL[item.status]}</span>
                        {item.status === 'failed' && item.error && (
                          <p className="mt-1 text-[11px] text-red-500 max-w-56 break-words" title={item.error}>
                            {item.error}
                          </p>
                        )}
                      </td>
                      <td className="px-3 py-2 text-right text-gray-600 dark:text-gray-400 text-xs whitespace-nowrap">
                        {formatJobRuntime(
                          {
                            status: item.status === 'completed' ? 'done' : item.status === 'running' ? 'running' : item.status,
                            created_at: item.createdAt > 0 ? item.createdAt / 1000 : lj?.created_at,
                            started_at: lj?.started_at,
                            finished_at: lj?.finished_at,
                          },
                          nowSec,
                        )}
                      </td>
                      <td className="px-3 py-2 text-right text-gray-600 dark:text-gray-400">{total || '-'}</td>
                      <td className="px-3 py-2 text-right text-gray-600 dark:text-gray-400">{total > 0 ? fmtRatio(proc, total) : proc || '-'}</td>
                      <td className="px-3 py-2 text-right text-gray-600 dark:text-gray-400 whitespace-nowrap">
                        {vlmDen > 0 ? fmtRatio(vlm, vlmDen) : vlm || '-'}
                        {vlmFail > 0 ? (
                          <>
                            <span className="ml-1 text-red-500">失败{vlmFail}</span>
                            {item.serverJobId && (
                              <button
                                type="button"
                                title="下载任务日志（定位失败原因）"
                                className="ml-1 inline-flex items-center justify-center w-5 h-5 rounded-full bg-red-100 text-red-600 hover:bg-red-200 text-[10px] leading-none align-middle"
                                onClick={(e) => {
                                  e.stopPropagation()
                                  api.downloadJobLog(item.serverJobId!).catch((err: any) =>
                                    showMsg(`日志下载失败: ${err?.message || err}`),
                                  )
                                }}
                              >
                                ⬇
                              </button>
                            )}
                            {item.serverJobId && (
                              <button
                                type="button"
                                title="仅重跑这部分失败的图片（新建任务）。仅对本功能上线后完成的任务有效；旧任务未落盘失败列表"
                                className="ml-1 inline-flex items-center justify-center w-5 h-5 rounded-full bg-amber-100 text-amber-600 hover:bg-amber-200 text-[10px] leading-none align-middle"
                                onClick={(e) => {
                                  e.stopPropagation()
                                  api.rerunFailedJob(item.serverJobId!)
                                    .then((resp) =>
                                      showMsg(`已提交重跑任务（失败 ${resp.failed_count} 张），可在「管理」章节查看进度`),
                                    )
                                    .catch((err: any) =>
                                      showMsg(`重跑失败: ${err?.message || err}`, 'error'),
                                    )
                                }}
                              >
                                ↻
                              </button>
                            )}
                          </>
                        ) : null}
                      </td>
                      <td className="px-3 py-2 text-right text-gray-600 dark:text-gray-400">{den > 0 ? fmtRatio(skipAll, den) : skipAll || '-'}</td>
                      <td className="px-3 py-2 text-right text-gray-600 dark:text-gray-400">{den > 0 ? fmtRatio(fail, den) : fail || '-'}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}

        {/* Running progress：建簇与 VLM 双进度条 */}
        {queue.filter(q => q.status === 'running').map((item, idx) => {
          const lj = item.lastJob
          const total = lj?.total || 0
          const proc = lj?.processed || 0
          const vlmDone = lj?.vlm_calls || 0
          const vlmFail = lj?.vlm_failed || 0
          const vlmTotal = lj?.new_centers || 0
          const clusterPct = total > 0 ? Math.min(proc / total, 1) : 0
          const vlmPct = vlmTotal > 0 ? Math.min(vlmDone / vlmTotal, 1) : 0
          const clusteringComplete = total > 0 && proc >= total
          const vlmComplete = vlmTotal === 0 || (vlmDone + vlmFail) >= vlmTotal
          const phaseLabel = !clusteringComplete
            ? '建簇中（CLIP + 双阈值）'
            : !vlmComplete
              ? 'VLM 标注中（与建簇并行，收尾等待）'
              : '收尾中'
          return (
            <div key={`${item.queueId}-${idx}`} className="mb-4 space-y-2">
              <p className="text-xs text-gray-600 dark:text-gray-400">
                运行中 <code className="bg-gray-100 px-1 rounded">{item.queueId}</code>
                · <span className="font-medium text-blue-700 dark:text-blue-300">{phaseLabel}</span>
                · 失败 {lj?.failed_so_far || 0}
                {vlmFail > 0 ? ` · VLM失败 ${vlmFail}` : ''}
              </p>
              <div>
                <div className="flex justify-between text-xs text-gray-500 mb-0.5">
                  <span>建簇</span>
                  <span>{proc} / {total || '—'}</span>
                </div>
                <div className="w-full bg-gray-200 rounded-full h-2">
                  <div
                    className={`h-2 rounded-full transition-all ${clusteringComplete ? 'bg-green-500' : 'bg-blue-500'}`}
                    style={{ width: `${clusterPct * 100}%` }}
                  />
                </div>
              </div>
              {(vlmTotal > 0 || vlmDone > 0) && (
                <div>
                  <div className="flex justify-between text-xs text-gray-500 mb-0.5">
                    <span>VLM 标注（簇中心）</span>
                    <span>{vlmDone} / {vlmTotal || '—'}</span>
                  </div>
                  <div className="w-full bg-gray-200 rounded-full h-2">
                    <div
                      className={`h-2 rounded-full transition-all ${vlmComplete ? 'bg-green-500' : 'bg-amber-500'}`}
                      style={{ width: `${vlmPct * 100}%` }}
                    />
                  </div>
                </div>
              )}
            </div>
          )
        })}

        <div className="flex gap-2">
          <button
            onClick={() => setQueueArmed(true)}
            disabled={!queue.some(q => q.status === 'queued')}
            className="px-4 py-2 text-sm bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            提交任务
          </button>
        </div>

        {displayQueue.length === 0 && (
          <p className="text-xs text-gray-400 dark:text-gray-500 mt-2">尚无已确认的任务；请先在「新建」中填写并点击「确认」加入队列。</p>
        )}
        </section>
      </ChapterSection>

      <ChapterSection title="管理" defaultCollapsed>
        <TaskQuerySection />
      </ChapterSection>

      <ConfirmDialog
        open={missingDirs !== null}
        title="部分目录不存在"
        message={
          <>
            <p>以下目录不存在：</p>
            <ul className="list-disc pl-5 font-mono text-xs break-all">
              {(missingDirs || []).map(d => <li key={d}>{d}</li>)}
            </ul>
            <p>不存在的目录会被流水线忽略。确定仍要提交吗？</p>
          </>
        }
        confirmLabel="仍要提交"
        onCancel={() => {
          setMissingDirs(null)
          showMsg('已取消')
        }}
        onConfirm={() => {
          const dirs = inputDirs.split('\n').map(s => s.trim()).filter(Boolean)
          const summary = dirs.length === 1 ? dirs[0] : `${dirs[0]} 等${dirs.length}项`
          setMissingDirs(null)
          enqueueCurrentForm(summary)
        }}
      />
    </div>
  )
}