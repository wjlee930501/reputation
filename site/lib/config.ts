const DEV_API_BASE = 'http://localhost:8000/api/v1/public'

function normalizeUrl(value: string): string {
  return value.replace(/\/$/, '')
}

function isLocalApiUrl(value: string): boolean {
  return /^https?:\/\/(localhost|127\.0\.0\.1|\[::1\])(?::|\/|$)/i.test(value)
}

export function getApiBase(required: true): string
export function getApiBase(required?: false): string | null
export function getApiBase(required = true): string | null {
  const value = process.env.NEXT_PUBLIC_API_URL?.trim()
  if (value) {
    const normalized = normalizeUrl(value)

    if (process.env.NODE_ENV === 'production' && isLocalApiUrl(normalized)) {
      if (!required) {
        return null
      }
      throw new Error('NEXT_PUBLIC_API_URL cannot point to localhost in production')
    }

    return normalized
  }

  if (process.env.NODE_ENV !== 'production') {
    return DEV_API_BASE
  }

  if (!required) {
    return null
  }

  throw new Error('NEXT_PUBLIC_API_URL is required in production')
}
