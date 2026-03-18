const BASE = '/api/admin'

export async function fetchAPI(path: string, options?: RequestInit) {
  const res = await fetch(`${BASE}${path}`, {
    headers: {
      'Content-Type': 'application/json',
      ...options?.headers,
    },
    ...options,
  })

  if (!res.ok) {
    const error = await res.text()
    throw new Error(error || `API error: ${res.status}`)
  }

  if (res.status === 204) return null
  return res.json()
}
