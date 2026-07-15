import { useState, useEffect, useRef, type ReactNode } from 'react'
import { api, type JobStatusResponse, type JobSummary } from '../api/client'
import TaskQuerySection from '../components/TaskQuerySection'
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

const YUV_TYPES = ['nv21', 'nv12', 'yuv420p']

interface QueueItem {
  queueId: string
  summary: string
  inputDirs: string[]
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

/** localStorage key */
const LS_LAST_SEEN = 'auto_tag_tasks_last_seen'

export default function Tasks() {
  const [inputDirs, setInputDirs] = useState('')
  const [rotLabel, setRotLabel] = useState(ROTATE_OPTIONS[0].label)
  const [mixedYuv, setMixedYuv] = useState(false)
  const [bYuv, setBYuv] = useState(false)
  const [yuvW, setYuvW] = useState(640)
  const [yuvH, setYuvH] = useState(480)
  const [yuvType, setYuvType] = useState('nv21')
  const [skipIfInDb, setSkipIfInDb] = useState(true)
  // 本地队列（当前 session 提交的 + 从后端拉取的）
  const [queue, setQueue] = useState<QueueItem[]>([])
  const [queueArmed, setQueueArmed] = useState(false)
  const [msg, setMsg] = useState('')
  // 时间点过滤
  const [lastSeenAt, setLastSeenAt] = useState<number>(() => {
    const stored = localStorage.getItem(LS_LAST_SEEN)
    return stored ? Number(stored) : 0
  })

  const showMsg = (text: string) => {
    setMsg(text)
    setTimeout(() => setMsg(''), 5000)
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
    showMsg('已隐藏此时间之前的任务记录（可在下方「查询」章节查看全部）')
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
        input_dirs: nextItem.inputDirs,
        rotate_angle: nextItem.rotateAngle || null,
        b_yuv_image: nextItem.bYuv,
        mixed_yuv: nextItem.mixedYuv,
        yuv_type: nextItem.yuvType,
        image_width: nextItem.yuvW,
        image_height: nextItem.yuvH,
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

  const confirmTask = async () => {
    const dirs = inputDirs.split('\n').map(s => s.trim()).filter(Boolean)
    if (dirs.length === 0) {
      showMsg('请至少填写一个输入目录')
      return
    }
    try {
      const check = await api.checkDirs(dirs)
      if (check.not_exist.length > 0) {
        const msg = `以下目录不存在：\n${check.not_exist.join('\n')}\n\n确定仍要提交吗？`
        const ok = window.confirm(msg + '\n\n（不存在的目录会被流水线忽略）')
        if (!ok) {
          showMsg('已取消')
          return
        }
      }
    } catch (e: any) {
      showMsg(`无法校验目录（已跳过验证）: ${e.message}`)
    }
    const summary = dirs.length === 1 ? dirs[0] : `${dirs[0]} 等${dirs.length}项`
    const newItem: QueueItem = {
      queueId: Math.random().toString(36).slice(2, 10),
      summary,
      inputDirs: dirs,
      rotateAngle: ROTATE_OPTIONS.find(o => o.label === rotLabel)?.value || '',
      mixedYuv,
      bYuv,
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

  const downloadTaskJson = () => {
    const data = {
      version: 1,
      input_dirs: inputDirs.split('\n').map(s => s.trim()).filter(Boolean),
      rotate_angle: ROTATE_OPTIONS.find(o => o.label === rotLabel)?.value || null,
      b_yuv_image: bYuv,
      mixed_yuv: mixedYuv,
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
        if (data.rotate_angle) {
          const opt = ROTATE_OPTIONS.find(o => o.value === data.rotate_angle)
          if (opt) setRotLabel(opt.label)
        }
        if (data.b_yuv_image != null) setBYuv(data.b_yuv_image)
        if (data.mixed_yuv != null) setMixedYuv(data.mixed_yuv)
        if (data.yuv_type) setYuvType(data.yuv_type)
        if (data.image_width) setYuvW(data.image_width)
        if (data.image_height) setYuvH(data.image_height)
        if (data.skip_if_in_db != null) setSkipIfInDb(data.skip_if_in_db)
        showMsg('已加载到表单')
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
        <div className="mb-4 px-4 py-2 rounded text-sm bg-blue-50 dark:bg-blue-950/50 text-blue-700 dark:text-blue-300 border border-blue-200 dark:border-blue-800">
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
          <div>
            <label className="block text-sm text-gray-600 dark:text-gray-400 mb-1">输入目录（每行一个绝对路径）</label>
            <textarea
              value={inputDirs}
              onChange={e => setInputDirs(e.target.value)}
              placeholder="/path/to/images"
              className="w-full border rounded px-3 py-2 text-sm font-mono"
              rows={4}
            />
          </div>
          <div>
            <label className="block text-sm text-gray-600 dark:text-gray-400 mb-1">rotate_angle（可选）</label>
            <select value={rotLabel} onChange={e => setRotLabel(e.target.value)} className="border rounded px-3 py-2 text-sm">
              {ROTATE_OPTIONS.map(o => <option key={o.value} value={o.label}>{o.label}</option>)}
            </select>
          </div>
          <div>
            <p className="text-sm text-gray-600 dark:text-gray-400 mb-2">YUV 相关设置</p>
            <div className="space-y-2">
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={mixedYuv} onChange={e => setMixedYuv(e.target.checked)} />
                混合目录（.nv21/.nv12/.yuv 按 YUV 读，其余按图）
              </label>
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={bYuv} onChange={e => setBYuv(e.target.checked)} />
                整批均为 YUV（与「混合目录」二选一通常只开其一）
              </label>
              <div className="flex gap-4">
                <div>
                  <label className="block text-xs text-gray-500">YUV 宽度</label>
                  <input type="number" value={yuvW} onChange={e => setYuvW(Number(e.target.value))} className="border rounded px-2 py-1 text-sm w-24" />
                </div>
                <div>
                  <label className="block text-xs text-gray-500">YUV 高度</label>
                  <input type="number" value={yuvH} onChange={e => setYuvH(Number(e.target.value))} className="border rounded px-2 py-1 text-sm w-24" />
                </div>
                <div>
                  <label className="block text-xs text-gray-500">YUV 类型</label>
                  <select value={yuvType} onChange={e => setYuvType(e.target.value)} className="border rounded px-2 py-1 text-sm">
                    {YUV_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
                  </select>
                </div>
              </div>
            </div>
          </div>
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
                      <td className="px-3 py-2 text-right text-gray-600 dark:text-gray-400">{vlmDen > 0 ? fmtRatio(vlm, vlmDen) : vlm || '-'}</td>
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
          const vlmTotal = lj?.new_centers || 0
          const clusterPct = total > 0 ? Math.min(proc / total, 1) : 0
          const vlmPct = vlmTotal > 0 ? Math.min(vlmDone / vlmTotal, 1) : 0
          const clusteringComplete = total > 0 && proc >= total
          const vlmComplete = vlmTotal === 0 || vlmDone >= vlmTotal
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

      <ChapterSection title="查询" defaultCollapsed>
        <TaskQuerySection />
      </ChapterSection>
    </div>
  )
}