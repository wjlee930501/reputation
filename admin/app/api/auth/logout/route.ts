import { NextResponse } from 'next/server'

export const runtime = 'nodejs'

export async function POST() {
  // Clears the admin_session cookie. Full token-version revocation (invalidating
  // outstanding HMAC tokens server-side) depends on the admin_users wiring landing
  // in a later wave — this only clears the cookie for now.
  const res = NextResponse.json({ ok: true })
  res.cookies.set('admin_session', '', {
    httpOnly: true,
    secure: process.env.NODE_ENV === 'production',
    sameSite: 'lax',
    path: '/',
    maxAge: 0,
  })
  return res
}
