import { revalidatePath } from 'next/cache'
import { NextResponse } from 'next/server'

import { constantTimeEqual } from '@/lib/constant-time'

export const runtime = 'nodejs'

const SECRET = process.env.SITE_REVALIDATE_SECRET || ''

interface RevalidateBody {
  paths?: string[]
}

export async function POST(request: Request) {
  if (!SECRET) {
    return NextResponse.json({ ok: false, error: 'revalidate disabled (no secret)' }, { status: 503 })
  }

  const provided = request.headers.get('x-revalidate-secret') || ''
  if (!constantTimeEqual(provided, SECRET)) {
    return NextResponse.json({ ok: false, error: 'unauthorized' }, { status: 401 })
  }

  let body: RevalidateBody
  try {
    body = await request.json()
  } catch {
    return NextResponse.json({ ok: false, error: 'invalid json body' }, { status: 400 })
  }

  const paths = Array.isArray(body.paths) ? body.paths.filter((p) => typeof p === 'string' && p.startsWith('/')) : []
  if (paths.length === 0) {
    return NextResponse.json({ ok: false, error: 'no valid paths' }, { status: 400 })
  }

  for (const path of paths) {
    revalidatePath(path)
  }

  return NextResponse.json({ ok: true, revalidated: paths })
}
