export const LOGIN_PROXY_TIMEOUT_MS = 10_000

type LoginFetchInput = {
  readonly headers: Record<string, string>
  readonly body: string
}

type LoginFetchErrorResponse = {
  readonly status: 502 | 504
  readonly error: string
}

export function buildLoginFetchInit(input: LoginFetchInput): RequestInit {
  return {
    method: 'POST',
    headers: input.headers,
    body: input.body,
    cache: 'no-store',
    signal: AbortSignal.timeout(LOGIN_PROXY_TIMEOUT_MS),
  }
}

export function mapLoginFetchError(error: unknown): LoginFetchErrorResponse {
  if (error instanceof DOMException && error.name === 'TimeoutError') {
    return { status: 504, error: 'Authentication service timed out' }
  }
  return { status: 502, error: 'Authentication service unavailable' }
}
