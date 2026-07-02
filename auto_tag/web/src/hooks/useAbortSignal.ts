import { useEffect, useRef } from 'react'

/**
 * 返回一个稳定的 AbortController，在组件卸载时自动 abort 未完成的请求。
 * 每次调用返回最新的 signal，适合传递给 api client 方法。
 */
export function useAbortSignal(): AbortSignal {
  const controllerRef = useRef<AbortController | null>(null)

  // 每次 render 都创建新 controller，abort 旧的
  if (controllerRef.current) {
    controllerRef.current.abort()
  }
  controllerRef.current = new AbortController()

  useEffect(() => {
    const controller = controllerRef.current
    return () => {
      controller?.abort()
    }
  }, [])

  return controllerRef.current.signal
}