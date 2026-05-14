import { NextResponse } from 'next/server'
import type { NextRequest } from 'next/server'

import { verifySessionToken } from '@/lib/session'

export const config = {
  matcher: ['/((?!_next/|favicon\\.ico$|robots\\.txt$|sitemap\\.xml$).*)'],
}

const PUBLIC_PATHS = new Set<string>(['/login'])
const PUBLIC_PREFIXES = ['/api/auth/']

function isPublicPath(pathname: string): boolean {
  if (PUBLIC_PATHS.has(pathname)) return true
  return PUBLIC_PREFIXES.some((prefix) => pathname.startsWith(prefix))
}

function buildLoginRedirect(req: NextRequest): NextResponse {
  const url = req.nextUrl.clone()
  const target = `${req.nextUrl.pathname}${req.nextUrl.search}`
  url.pathname = '/login'
  url.search = target && target !== '/' ? `?redirect=${encodeURIComponent(target)}` : ''
  const res = NextResponse.redirect(url)
  res.cookies.delete('admin_session')
  return res
}

function buildUnauthorizedJson(): NextResponse {
  return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
}

export async function proxy(req: NextRequest) {
  const { pathname } = req.nextUrl
  if (isPublicPath(pathname)) return NextResponse.next()

  const sessionSecret = process.env.ADMIN_SESSION_SECRET
  if (!sessionSecret) {
    return NextResponse.json({ error: 'Server misconfigured' }, { status: 500 })
  }

  const token = req.cookies.get('admin_session')?.value
  const isValid = token ? await verifySessionToken(token, sessionSecret) : false

  if (isValid) return NextResponse.next()

  if (pathname.startsWith('/api/')) {
    return buildUnauthorizedJson()
  }
  return buildLoginRedirect(req)
}
