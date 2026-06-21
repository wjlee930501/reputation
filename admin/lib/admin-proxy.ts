export const ADMIN_PROXY_TIMEOUT_MS = 15_000
// 외부 스크랩(홈/블로그/네이버) + LLM 추출로 20~40초 걸리는 자동 채우기 같은
// 느린 엔드포인트용 상향 타임아웃. 일반 호출은 기본 15초를 유지한다.
export const ADMIN_PROXY_SLOW_TIMEOUT_MS = 60_000

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
