export const ADMIN_PROXY_TIMEOUT_MS = 15_000

export function buildAdminProxyFetchInit(options: {
  method: string
  headers: Record<string, string>
  body?: ArrayBuffer
}): RequestInit {
  return {
    method: options.method,
    headers: options.headers,
    cache: 'no-store',
    signal: AbortSignal.timeout(ADMIN_PROXY_TIMEOUT_MS),
    ...(options.body ? { body: options.body } : {}),
  }
}

export function mapAdminProxyFetchError(error: unknown): { status: number; error: string } {
  if (error instanceof DOMException && error.name === 'TimeoutError') {
    return { status: 504, error: 'Admin service timed out' }
  }
  return { status: 502, error: 'Admin service unavailable' }
}
