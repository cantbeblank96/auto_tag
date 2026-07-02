import { api } from '../api/client'

/** 重启后端后轮询 /api/health 直至恢复（先等待旧进程退出与新脚本拉起）。 */
export async function waitForBackendHealthy(
  maxAttempts = 45,
  intervalMs = 1000,
  initialDelayMs = 1200,
): Promise<boolean> {
  await new Promise((r) => setTimeout(r, initialDelayMs))
  for (let i = 0; i < maxAttempts; i++) {
    try {
      await api.health()
      return true
    } catch {
      /* retry */
    }
    await new Promise((r) => setTimeout(r, intervalMs))
  }
  return false
}
