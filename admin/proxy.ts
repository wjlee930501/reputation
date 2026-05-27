import { NextRequest, NextResponse } from 'next/server'

import { buildAdminAuthProxyResponse } from './lib/auth-proxy'

export const config = {
  matcher: ['/((?!_next/|favicon\\.ico$|robots\\.txt$|sitemap\\.xml$).*)'],
}

export async function proxy(req: NextRequest) {
  return (await buildAdminAuthProxyResponse(req)) ?? NextResponse.next()
}
