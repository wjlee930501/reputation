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
    throw new Error(error || `API error: ${res.status}`)
  }

  if (res.status === 204) return null
  const text = await res.text()
  return text ? JSON.parse(text) : null
}
