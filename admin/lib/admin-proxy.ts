export const ADMIN_PROXY_TIMEOUT_MS = 15_000
// 외부 스크랩/LLM은 호출당 60초, 최대 3회 재시도할 수 있다. UI 프록시가 먼저
// 끊겨 백엔드만 커밋되는 상태를 피하려고 재시도 여유를 포함한다.
export const ADMIN_PROXY_SLOW_TIMEOUT_MS = 210_000

export function isSlowAdminProxyPath(path: string): boolean {
  return (
    path.endsWith('/profile/autofill') ||
    /\/essence\/sources\/[^/]+\/process$/.test(path) ||
    path.endsWith('/essence/philosophy/draft')
  )
}

export function adminProxyTimeoutMsForPath(path: string): number {
  return isSlowAdminProxyPath(path) ? ADMIN_PROXY_SLOW_TIMEOUT_MS : ADMIN_PROXY_TIMEOUT_MS
}

export function buildAdminProxyFetchInit(options: {
  method: string
  headers: Record<string, string>
  body?: ArrayBuffer
  timeoutMs?: number
}): RequestInit {
  return {
    method: options.method,
    headers: options.headers,
    cache: 'no-store',
    signal: AbortSignal.timeout(options.timeoutMs ?? ADMIN_PROXY_TIMEOUT_MS),
    ...(options.body ? { body: options.body } : {}),
  }
}

export function mapAdminProxyFetchError(error: unknown): { status: number; error: string } {
  if (error instanceof DOMException && error.name === 'TimeoutError') {
    return { status: 504, error: 'Admin service timed out' }
  }
  return { status: 502, error: 'Admin service unavailable' }
}
