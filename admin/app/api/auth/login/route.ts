import { timingSafeEqual } from 'crypto'
import { NextRequest, NextResponse } from 'next/server'

import { generateSessionToken } from '@/lib/session'

export const runtime = 'nodejs'

const MAX_LOGIN_ATTEMPTS = 5
const LOGIN_WINDOW_MS = 15 * 60 * 1000

type LoginAttempt = {
  count: number
  resetAt: number
}

const loginAttempts = new Map<string, LoginAttempt>()

function getClientKey(req: NextRequest): string {
  const forwardedFor = req.headers.get('x-forwarded-for')?.split(',')[0]?.trim()
  const realIp = req.headers.get('x-real-ip')?.trim()
  return forwardedFor || realIp || 'unknown'
}

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
  const payload = await req.json().catch(() => null)
  const password = typeof payload?.password === 'string' ? payload.password : ''
  const loginPassword = process.env.ADMIN_LOGIN_PASSWORD
  const sessionSecret = process.env.ADMIN_SESSION_SECRET

  if (!loginPassword || !sessionSecret) {
    return NextResponse.json({ error: 'Server misconfigured' }, { status: 500 })
  }

  const clientKey = getClientKey(req)
  const now = Date.now()
  if (isRateLimited(clientKey, now)) {
    return NextResponse.json({ error: 'Too many login attempts' }, { status: 429 })
  }

  // Timing-safe comparison for password
  const pwBuf = Buffer.from(password)
  const loginPasswordBuf = Buffer.from(loginPassword)
  if (pwBuf.length !== loginPasswordBuf.length || !timingSafeEqual(pwBuf, loginPasswordBuf)) {
    recordFailedAttempt(clientKey, now)
    return NextResponse.json({ error: 'Invalid password' }, { status: 401 })
  }

  clearFailedAttempts(clientKey)
  const token = await generateSessionToken(sessionSecret)

  const res = NextResponse.json({ ok: true })
  res.cookies.set('admin_session', token, {
    httpOnly: true,
    secure: process.env.NODE_ENV === 'production',
    sameSite: 'lax',
    path: '/',
    maxAge: 60 * 60 * 24 * 7, // 7 days
  })
  return res
}
