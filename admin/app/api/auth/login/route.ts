import { timingSafeEqual } from 'crypto'
import { NextRequest, NextResponse } from 'next/server'

import { generateSessionToken } from '@/lib/session'

export const runtime = 'nodejs'

export async function POST(req: NextRequest) {
  const payload = await req.json().catch(() => null)
  const password = typeof payload?.password === 'string' ? payload.password : ''
  const secret = process.env.ADMIN_SESSION_SECRET

  if (!secret) {
    return NextResponse.json({ error: 'Server misconfigured' }, { status: 500 })
  }

  // Timing-safe comparison for password
  const pwBuf = Buffer.from(password)
  const secretBuf = Buffer.from(secret)
  if (pwBuf.length !== secretBuf.length || !timingSafeEqual(pwBuf, secretBuf)) {
    return NextResponse.json({ error: 'Invalid password' }, { status: 401 })
  }

  const token = await generateSessionToken(secret)

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
