import { NextResponse } from 'next/server'
import type { NextRequest } from 'next/server'
import { createHmac, timingSafeEqual } from 'crypto'

function verifySessionToken(token: string, secret: string): boolean {
  const parts = token.split('.')
  if (parts.length !== 2) return false
  const [nonce, sig] = parts
  const expected = createHmac('sha256', secret).update(nonce).digest('hex')
  try {
    return timingSafeEqual(Buffer.from(sig, 'hex'), Buffer.from(expected, 'hex'))
  } catch {
    return false
  }
}

export function middleware(req: NextRequest) {
  const sessionSecret = process.env.ADMIN_SESSION_SECRET
  if (!sessionSecret) {
    return NextResponse.json({ error: 'Server misconfigured' }, { status: 500 })
  }

  const cookie = req.cookies.get('admin_session')?.value
  if (!cookie || !verifySessionToken(cookie, sessionSecret)) {
    const loginUrl = new URL('/login', req.url)
    loginUrl.searchParams.set('redirect', req.nextUrl.pathname)
    return NextResponse.redirect(loginUrl)
  }

  return NextResponse.next()
}

export const config = {
  matcher: [
    '/api/admin/:path*',
    '/hospitals/:path*',
    '/((?!login|_next|api/auth|favicon.ico).*)',
  ],
}
