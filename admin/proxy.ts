import { NextResponse } from 'next/server'
import type { NextRequest } from 'next/server'

import { verifySessionToken } from '@/lib/session'

export async function proxy(req: NextRequest) {
  const sessionSecret = process.env.ADMIN_SESSION_SECRET
  if (!sessionSecret) {
    return NextResponse.json({ error: 'Server misconfigured' }, { status: 500 })
  }

  const cookie = req.cookies.get('admin_session')?.value
  if (!cookie || !(await verifySessionToken(cookie, sessionSecret))) {
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
