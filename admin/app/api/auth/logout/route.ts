import { NextRequest, NextResponse } from 'next/server.js'

import { getBackendUrl } from '../../../../lib/backend.ts'
import { ADMIN_CSRF_COOKIE_NAME } from '../../../../lib/csrf.ts'
import { hasValidAdminCsrfToken, hasValidSameOrigin } from '../../../../lib/security.ts'
import { revokeAdminSession } from '../../../../lib/session-revocation.ts'
import { readSessionToken } from '../../../../lib/session.ts'

export const runtime = 'nodejs'

function noStoreResponse(body: BodyInit, init: ResponseInit): NextResponse {
  const res = new NextResponse(body, init)
  res.headers.set('cache-control', 'no-store, private')
  return res
}

function backendUrlOrNull(): string | null {
  try {
    return getBackendUrl()
  } catch (error) {
    if (error instanceof Error) return null
    throw error
  }
}

export async function POST(req: NextRequest) {
  if (!hasValidSameOrigin(req)) {
    return noStoreResponse('Forbidden', { status: 403 })
  }

  const sessionSecret = process.env.ADMIN_SESSION_SECRET
  const sessionToken = req.cookies.get('admin_session')?.value
  const session = sessionSecret && sessionToken ? await readSessionToken(sessionToken, sessionSecret) : null
  if (session?.csrfToken && !hasValidAdminCsrfToken(req, session)) {
    return noStoreResponse('Forbidden', { status: 403 })
  }
  if (session && sessionToken) {
    const adminKey = process.env.ADMIN_SECRET_KEY || ''
    if (!adminKey) {
      return noStoreResponse('Server misconfigured', { status: 500 })
    }

    const backendUrl = backendUrlOrNull()
    if (!backendUrl) {
      return noStoreResponse('Server misconfigured', { status: 500 })
    }

    const revocationStatus = await revokeAdminSession({
      backendUrl,
      adminKey,
      sessionToken,
      expiresAtMs: session.expiresAt,
    })
    if (revocationStatus !== 'revoked') {
      return noStoreResponse('Admin session state unavailable', { status: 503 })
    }
  }

  const res = NextResponse.json({ ok: true })
  res.headers.set('cache-control', 'no-store, private')
  res.cookies.set('admin_session', '', {
    httpOnly: true,
    secure: process.env.NODE_ENV === 'production',
    sameSite: 'lax',
    path: '/',
    maxAge: 0,
  })
  res.cookies.set(ADMIN_CSRF_COOKIE_NAME, '', {
    httpOnly: false,
    secure: process.env.NODE_ENV === 'production',
    sameSite: 'lax',
    path: '/',
    maxAge: 0,
  })
  return res
}
