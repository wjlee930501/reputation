import { NextRequest, NextResponse } from 'next/server'

import { getBackendUrl } from '@/lib/backend'
import { getLoginRateLimitKey, hasValidSameOrigin } from '@/lib/security'
import { generateSessionToken } from '@/lib/session'

export const runtime = 'nodejs'

const MAX_LOGIN_ATTEMPTS = 5
const LOGIN_WINDOW_MS = 15 * 60 * 1000
const SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 7

type AdminAccountResponse = {
  id: string
  email: string
  name: string
  role: string
}

type LoginAttempt = {
  count: number
  resetAt: number
}

const loginAttempts = new Map<string, LoginAttempt>()

function isRateLimited(key: string, now: number): boolean {
  const attempt = loginAttempts.get(key)
  if (!attempt || attempt.resetAt <= now) {
    return false
  }
  return attempt.count >= MAX_LOGIN_ATTEMPTS
}

function recordFailedAttempt(key: string, now: number) {
  const attempt = loginAttempts.get(key)
  if (!attempt || attempt.resetAt <= now) {
    loginAttempts.set(key, { count: 1, resetAt: now + LOGIN_WINDOW_MS })
    return
  }
  attempt.count += 1
}

function clearFailedAttempts(key: string) {
  loginAttempts.delete(key)
}

export async function POST(req: NextRequest) {
  if (!hasValidSameOrigin(req)) {
    return new NextResponse('Forbidden', { status: 403 })
  }

  const payload = await req.json().catch(() => null)
  const email = typeof payload?.email === 'string' ? payload.email.trim().toLowerCase() : ''
  const password = typeof payload?.password === 'string' ? payload.password : ''
  const sessionSecret = process.env.ADMIN_SESSION_SECRET
  const adminKey = process.env.ADMIN_SECRET_KEY

  if (!sessionSecret || !adminKey) {
    return NextResponse.json({ error: 'Server misconfigured' }, { status: 500 })
  }

  // Throttle by BOTH client IP and target email. The email key cannot be rotated
  // away, so it bounds brute-force even when the IP is unavailable (null key) or
  // spoofed via forwarding headers.
  const clientKey = getLoginRateLimitKey(req)
  const emailKey = email ? `email:${email}` : null
  const throttleKeys = [clientKey, emailKey].filter((k): k is string => Boolean(k))
  const now = Date.now()
  if (throttleKeys.some((key) => isRateLimited(key, now))) {
    return NextResponse.json({ error: 'Too many login attempts' }, { status: 429 })
  }

  if (!email || !password) {
    throttleKeys.forEach((key) => recordFailedAttempt(key, now))
    return NextResponse.json({ error: 'Invalid credentials' }, { status: 401 })
  }

  let backendUrl: string
  try {
    backendUrl = getBackendUrl()
  } catch {
    return NextResponse.json({ error: 'Server misconfigured' }, { status: 500 })
  }

  let account: AdminAccountResponse
  try {
    const authResponse = await fetch(new URL('/api/v1/admin/auth/login', backendUrl), {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        'X-Admin-Key': adminKey,
      },
      body: JSON.stringify({ email, password }),
      cache: 'no-store',
    })

    if (!authResponse.ok) {
      // CDX-M3: 전역(인스턴스 무관) 스로틀은 backend가 Redis로 수행한다 — 429는 그대로 전달.
      // 아래 Map 카운터는 인스턴스-로컬 보조 방어로 유지.
      if (authResponse.status === 429) {
        return NextResponse.json({ error: 'Too many login attempts' }, { status: 429 })
      }
      throttleKeys.forEach((key) => recordFailedAttempt(key, now))
      return NextResponse.json({ error: 'Invalid credentials' }, { status: 401 })
    }

    account = (await authResponse.json()) as AdminAccountResponse
  } catch {
    return NextResponse.json({ error: 'Authentication service unavailable' }, { status: 503 })
  }

  throttleKeys.forEach((key) => clearFailedAttempts(key))
  const token = await generateSessionToken(sessionSecret, SESSION_MAX_AGE_SECONDS, {
    accountId: account.id,
    email: account.email,
    name: account.name,
    role: account.role,
  })

  const res = NextResponse.json({
    ok: true,
    account: { email: account.email, name: account.name, role: account.role },
  })
  res.cookies.set('admin_session', token, {
    httpOnly: true,
    secure: process.env.NODE_ENV === 'production',
    sameSite: 'lax',
    path: '/',
    maxAge: SESSION_MAX_AGE_SECONDS,
  })
  return res
}
