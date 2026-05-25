const BASE = '/api/admin'

function normalizePath(path: string): string {
  if (path.startsWith('/api/admin/')) return path.slice('/api/admin'.length)
  if (path.startsWith('/admin/')) return path.slice('/admin'.length)
  return path.startsWith('/') ? path : `/${path}`
}

export async function fetchAPI(path: string, options?: RequestInit) {
  const { headers: customHeaders, ...rest } = options ?? {}
  const res = await fetch(`${BASE}${normalizePath(path)}`, {
    ...rest,
    headers: {
      'Content-Type': 'application/json',
      ...customHeaders,
    },
  })

  if (!res.ok) {
    const error = await res.text()
    const detail = readErrorMessage(error)
    throw new Error(detail || statusMessage(res.status) || `서버 오류 (${res.status})`)
  }

  if (res.status === 204) return null
  const text = await res.text()
  return text ? JSON.parse(text) : null
}

function statusMessage(status: number): string | null {
  const messages: Record<number, string> = {
    400: '입력값이 올바르지 않습니다.',
    401: '인증이 필요합니다. 다시 로그인해 주세요.',
    403: '접근 권한이 없습니다.',
    404: '요청한 정보를 찾을 수 없습니다.',
    409: '이미 처리 중이거나 충돌이 발생했습니다.',
    422: '입력값 검증에 실패했습니다.',
    429: '요청이 너무 많습니다. 잠시 후 다시 시도해 주세요.',
    500: '서버 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.',
    502: '서버가 일시적으로 응답하지 않습니다.',
    503: '서버 점검 중입니다. 잠시 후 다시 시도해 주세요.',
  }
  return messages[status] || null
}

function readErrorMessage(body: string): string {
  if (!body) return ''
  try {
    const parsed = JSON.parse(body) as unknown
    if (parsed && typeof parsed === 'object' && 'detail' in parsed) {
      const detail = (parsed as { detail?: unknown }).detail
      if (typeof detail === 'string') return detail
      if (Array.isArray(detail)) {
        return detail
          .map((entry) => {
            if (entry && typeof entry === 'object' && 'msg' in entry) {
              return String((entry as { msg: unknown }).msg)
            }
            return String(entry)
          })
          .join('\n')
      }
      if (detail != null) return JSON.stringify(detail)
    }
  } catch {
    // Fall through to the raw text body.
  }
  return body
}
