const BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000/api/v1'
const ADMIN_KEY = process.env.NEXT_PUBLIC_ADMIN_KEY || ''

export async function fetchAPI(path: string, options?: RequestInit) {
  const res = await fetch(`${BASE}${path}`, {
    headers: {
      'Content-Type': 'application/json',
      'X-Admin-Key': ADMIN_KEY,
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
