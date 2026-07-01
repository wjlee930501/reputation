import { hashSessionToken } from './session.ts'

export type AdminSessionRevocationStatus = 'active' | 'revoked' | 'unavailable'

export const ADMIN_SESSION_REVOCATION_TIMEOUT_MS = 3_000

interface CheckAdminSessionRevocationOptions {
  backendUrl: string
  adminKey: string
  sessionToken: string
}

interface RevokeAdminSessionOptions extends CheckAdminSessionRevocationOptions {
  expiresAtMs: number
}

function adminAuthUrl(backendUrl: string, path: string): string {
  return new URL(`/api/v1/admin/auth/${path}`, backendUrl).toString()
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function buildRevocationSignal(): AbortSignal {
  return AbortSignal.timeout(ADMIN_SESSION_REVOCATION_TIMEOUT_MS)
}

function isExpectedRevocationFailure(error: unknown): boolean {
  return error instanceof DOMException || error instanceof TypeError || error instanceof SyntaxError
}

export async function checkAdminSessionRevocation({
  backendUrl,
  adminKey,
  sessionToken,
}: CheckAdminSessionRevocationOptions): Promise<AdminSessionRevocationStatus> {
  const tokenHash = await hashSessionToken(sessionToken)
  try {
    const res = await fetch(adminAuthUrl(backendUrl, `sessions/${tokenHash}/revocation`), {
      headers: { 'X-Admin-Key': adminKey },
      cache: 'no-store',
      signal: buildRevocationSignal(),
    })
    if (!res.ok) return 'unavailable'

    const data: unknown = await res.json()
    if (!isRecord(data) || typeof data.revoked !== 'boolean') return 'unavailable'
    return data.revoked ? 'revoked' : 'active'
  } catch (error) {
    if (!isExpectedRevocationFailure(error)) throw error
    return 'unavailable'
  }
}

export async function revokeAdminSession({
  backendUrl,
  adminKey,
  sessionToken,
  expiresAtMs,
}: RevokeAdminSessionOptions): Promise<AdminSessionRevocationStatus> {
  const tokenHash = await hashSessionToken(sessionToken)
  try {
    const res = await fetch(adminAuthUrl(backendUrl, 'sessions/revoke'), {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Admin-Key': adminKey,
      },
      body: JSON.stringify({
        token_hash: tokenHash,
        expires_at: new Date(expiresAtMs).toISOString(),
      }),
      cache: 'no-store',
      signal: buildRevocationSignal(),
    })
    if (!res.ok) return 'unavailable'

    const data: unknown = await res.json()
    if (!isRecord(data) || typeof data.revoked !== 'boolean') return 'unavailable'
    return data.revoked ? 'revoked' : 'active'
  } catch (error) {
    if (!isExpectedRevocationFailure(error)) throw error
    return 'unavailable'
  }
}
