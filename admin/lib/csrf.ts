export const ADMIN_CSRF_COOKIE_NAME = 'admin_csrf'
export const ADMIN_CSRF_HEADER_NAME = 'X-Admin-CSRF-Token'

const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS'])

export function isStateChangingMethod(method = 'GET'): boolean {
  return !SAFE_METHODS.has(method.toUpperCase())
}

export function readAdminCsrfCookie(cookieSource: string): string | null {
  for (const part of cookieSource.split(';')) {
    const [rawName, ...rawValue] = part.split('=')
    if (rawName.trim() !== ADMIN_CSRF_COOKIE_NAME) continue
    const value = rawValue.join('=').trim()
    if (!value) return null
    try {
      return decodeURIComponent(value)
    } catch (error) {
      if (!(error instanceof URIError)) throw error
      return value
    }
  }
  return null
}

export function buildAdminCsrfHeaders(method = 'GET'): Record<string, string> {
  if (!isStateChangingMethod(method)) return {}
  if (typeof document === 'undefined') return {}

  const token = readAdminCsrfCookie(document.cookie)
  return token ? { [ADMIN_CSRF_HEADER_NAME]: token } : {}
}
