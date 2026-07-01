import { NextRequest, NextResponse } from 'next/server'

import { getBackendUrl } from '@/lib/backend'
import { ADMIN_CSRF_COOKIE_NAME } from '@/lib/csrf'
import { buildLoginFetchInit, mapLoginFetchError } from '@/lib/login-proxy'
import { clientIpFromForwardedHeaders, hasValidSameOrigin } from '@/lib/security'
import { generateCsrfToken, generateSessionToken } from '@/lib/session'

export const runtime = 'nodejs'

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

export async function POST(req: NextRequest) {
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

  // 백엔드 per-IP 로그인 스로틀은 요청 IP를 키로 쓴다 — BFF 경유 로그인은 admin 서버
  // egress IP로 합쳐져 AE 한 명의 실패가 전원을 잠근다. SITE_BFF_SECRET이 설정되면
  // X-BFF-Auth + X-Visitor-IP 쌍으로 실제 방문자 IP를 인증 전달한다
  // (backend get_request_ip가 우선 채택 — site/app/api/leads/route.ts와 동일 패턴).
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
      })
    )

    if (!authResponse.ok) {
      // CDX-M3: 전역(인스턴스 무관) 스로틀은 backend가 Redis로 수행한다 — 429는 그대로 전달.
      if (authResponse.status === 429) {
        return jsonNoStore({ error: 'Too many login attempts' }, { status: 429 })
      }
      return jsonNoStore({ error: 'Invalid credentials' }, { status: 401 })
    }

    account = (await authResponse.json()) as AdminAccountResponse
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
