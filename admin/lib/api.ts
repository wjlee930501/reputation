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
    throw new Error(readErrorMessage(error) || `API error: ${res.status}`)
  }

  if (res.status === 204) return null
  const text = await res.text()
  return text ? JSON.parse(text) : null
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
