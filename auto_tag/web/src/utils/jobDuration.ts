/** 任务运行时长格式化与计算（秒级 Unix 时间戳）。 */

export function formatDurationSeconds(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds) || seconds < 0) return '-'
  const s = Math.floor(seconds)
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  const rem = s % 60
  if (m < 60) return rem > 0 ? `${m}m ${rem}s` : `${m}m`
  const h = Math.floor(m / 60)
  const remM = m % 60
  return remM > 0 ? `${h}h ${remM}m` : `${h}h`
}

export interface JobTimingFields {
  created_at?: number
  started_at?: number
  finished_at?: number
  status?: string
}

/** 运行时长（秒）：优先 started_at→finished_at；运行中用 now 估算。 */
export function jobRuntimeSeconds(
  job: JobTimingFields,
  nowSec: number = Date.now() / 1000,
): number | null {
  const start = job.started_at || job.created_at || 0
  if (start <= 0) return null
  if (job.finished_at && job.finished_at > 0) {
    return Math.max(0, job.finished_at - start)
  }
  if (job.status === 'running' || job.status === 'queued') {
    if (job.status === 'queued') return null
    return Math.max(0, nowSec - start)
  }
  return null
}

export function formatJobRuntime(
  job: JobTimingFields,
  nowSec: number = Date.now() / 1000,
): string {
  if (job.status === 'queued') return '排队中'
  return formatDurationSeconds(jobRuntimeSeconds(job, nowSec))
}
