import { NextRequest, NextResponse } from 'next/server'

import { hasValidSameOrigin } from '@/lib/security'

export const runtime = 'nodejs'

function noStoreResponse(body: BodyInit, init: ResponseInit): NextResponse {
  const res = new NextResponse(body, init)
  res.headers.set('cache-control', 'no-store, private')
  return res
}

export async function POST(req: NextRequest) {
  if (!hasValidSameOrigin(req)) {
    return noStoreResponse('Forbidden', { status: 403 })
  }

  const res = NextResponse.json({ ok: true })
  res.headers.set('cache-control', 'no-store, private')
  res.cookies.set('admin_session', '', {
    httpOnly: true,
    secure: process.env.NODE_ENV === 'production',
    sameSite: 'lax',
    path: '/',
    maxAge: 0,
  })
  return res
}
