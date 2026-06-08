import { NextRequest, NextResponse } from 'next/server'

import { hasValidSameOrigin } from '@/lib/security'

export const runtime = 'nodejs'

export async function POST(req: NextRequest) {
  // CSRF guard: only accept same-origin POSTs so a cross-site form cannot force logout.
  if (!hasValidSameOrigin(req)) {
    return new NextResponse('Forbidden', { status: 403 })
  }

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
