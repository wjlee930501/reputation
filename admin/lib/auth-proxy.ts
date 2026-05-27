import { NextResponse } from 'next/server.js'
import type { NextRequest } from 'next/server.js'

import { verifySessionToken } from './session.ts'

type AuthProxyRequest = {
  nextUrl: {
    pathname: string
    search: string
    clone(): URL
  }
  cookies: {
    get(name: string): { value: string } | undefined
  }
}

export const adminAuthProxyConfig = {
  matcher: ['/((?!_next/|favicon\\.ico$|robots\\.txt$|sitemap\\.xml$).*)'],
}

const PUBLIC_PATHS = new Set<string>(['/login'])
const PUBLIC_PREFIXES = ['/api/auth/']

function isPublicPath(pathname: string): boolean {
  if (PUBLIC_PATHS.has(pathname)) return true
  return PUBLIC_PREFIXES.some((prefix) => pathname.startsWith(prefix))
}

function buildLoginRedirect(req: AuthProxyRequest): NextResponse {
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

export async function buildAdminAuthProxyResponse(req: AuthProxyRequest): Promise<NextResponse | undefined> {
  const { pathname } = req.nextUrl
  if (isPublicPath(pathname)) return undefined

  const sessionSecret = process.env.ADMIN_SESSION_SECRET
  if (!sessionSecret) {
    return NextResponse.json({ error: 'Server misconfigured' }, { status: 500 })
  }

  const token = req.cookies.get('admin_session')?.value
  const isValid = token ? await verifySessionToken(token, sessionSecret) : false

  if (isValid) return undefined

  if (pathname.startsWith('/api/')) {
    return buildUnauthorizedJson()
  }
  return buildLoginRedirect(req)
}
