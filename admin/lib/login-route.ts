import { NextRequest, NextResponse } from 'next/server.js'

import { getBackendUrl } from './backend.ts'
import { ADMIN_CSRF_COOKIE_NAME } from './csrf.ts'
import { buildLoginFetchInit, mapLoginFetchError } from './login-proxy.ts'
import { clientIpFromForwardedHeaders, hasValidSameOrigin } from './security.ts'
import { generateCsrfToken, generateSessionToken } from './session.ts'

const SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 7

type AdminAccountResponse = {
  id: string
  email: string
  name: string
  role: string
}

function jsonNoStore(body: unknown, init?: ResponseInit): NextResponse {
  const res = NextResponse.json(body, init)
  res.headers.set('Cache-Control', 'no-store, private')
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

function isAdminAccountResponse(value: unknown): value is AdminAccountResponse {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return false
  const account = value as Record<string, unknown>
  return (
    typeof account.id === 'string' &&
    account.id.trim().length > 0 &&
    typeof account.email === 'string' &&
    account.email.trim().length > 0 &&
    typeof account.name === 'string' &&
    account.name.trim().length > 0 &&
    typeof account.role === 'string' &&
    account.role.trim().length > 0
  )
}

export async function handleAdminLogin(req: NextRequest) {
  if (!hasValidSameOrigin(req)) {
    return new NextResponse('Forbidden', {
      status: 403,
      headers: { 'Cache-Control': 'no-store, private' },
    })
  }

  const payload = await req.json().catch(() => null)
  const email = typeof payload?.email === 'string' ? payload.email.trim().toLowerCase() : ''
  const password = typeof payload?.password === 'string' ? payload.password : ''
  const sessionSecret = process.env.ADMIN_SESSION_SECRET
  const adminKey = process.env.ADMIN_SECRET_KEY

  if (!sessionSecret || !adminKey) {
    return jsonNoStore({ error: 'Server misconfigured' }, { status: 500 })
  }

  if (!email || !password) {
    return jsonNoStore({ error: 'Invalid credentials' }, { status: 401 })
  }

  const backendUrl = backendUrlOrNull()
  if (!backendUrl) {
    return jsonNoStore({ error: 'Server misconfigured' }, { status: 500 })
  }

  const upstreamHeaders: Record<string, string> = {
    'content-type': 'application/json',
    'X-Admin-Key': adminKey,
  }
  const bffSecret = (process.env.SITE_BFF_SECRET || '').trim()
  const visitorIp = clientIpFromForwardedHeaders(req.headers)
  if (bffSecret && visitorIp) {
    upstreamHeaders['X-BFF-Auth'] = bffSecret
    upstreamHeaders['X-Visitor-IP'] = visitorIp
  }

  let account: AdminAccountResponse
  try {
    const authResponse = await fetch(
      new URL('/api/v1/admin/auth/login', backendUrl),
      buildLoginFetchInit({
        headers: upstreamHeaders,
        body: JSON.stringify({ email, password }),
      }),
    )

    if (!authResponse.ok) {
      if (authResponse.status === 429) {
        return jsonNoStore({ error: 'Too many login attempts' }, { status: 429 })
      }
      return jsonNoStore({ error: 'Invalid credentials' }, { status: 401 })
    }

    const data: unknown = await authResponse.json()
    if (!isAdminAccountResponse(data)) {
      return jsonNoStore({ error: 'Invalid auth response' }, { status: 502 })
    }
    account = data
  } catch (error) {
    const mapped = mapLoginFetchError(error)
    return jsonNoStore({ error: mapped.error }, { status: mapped.status })
  }

  const csrfToken = generateCsrfToken()
  const token = await generateSessionToken(sessionSecret, SESSION_MAX_AGE_SECONDS, {
    accountId: account.id,
    email: account.email,
    name: account.name,
    role: account.role,
    csrfToken,
  })

  const res = NextResponse.json({
    ok: true,
    account: { email: account.email, name: account.name, role: account.role },
  })
  res.headers.set('Cache-Control', 'no-store, private')
  res.cookies.set('admin_session', token, {
    httpOnly: true,
    secure: process.env.NODE_ENV === 'production',
    sameSite: 'lax',
    path: '/',
    maxAge: SESSION_MAX_AGE_SECONDS,
  })
  res.cookies.set(ADMIN_CSRF_COOKIE_NAME, csrfToken, {
    httpOnly: false,
    secure: process.env.NODE_ENV === 'production',
    sameSite: 'lax',
    path: '/',
    maxAge: SESSION_MAX_AGE_SECONDS,
  })
  return res
}
