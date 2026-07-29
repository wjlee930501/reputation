import { NextResponse } from 'next/server'
import { getApiBase } from '@/lib/config'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'

/**
 * 리포트 PDF 프록시.
 *
 * 백엔드가 붙인 no-store·noindex·no-referrer를 여기서도 다시 붙인다 — 프록시가
 * 헤더를 떨어뜨리면 CDN이 개인 리포트를 저장할 수 있다 (PRD F5-4).
 */
export async function GET(_request: Request, { params }: { params: Promise<{ token: string }> }) {
  const { token } = await params
  const guard = {
    'Cache-Control': 'no-store',
    'X-Robots-Tag': 'noindex, nofollow',
    'Referrer-Policy': 'no-referrer',
  }
  let upstream: Response
  try {
    upstream = await fetch(`${getApiBase()}/public/diagnosis/${encodeURIComponent(token)}`, {
      cache: 'no-store',
    })
  } catch {
    return NextResponse.json({ error: '리포트를 불러오지 못했습니다.' }, { status: 502, headers: guard })
  }

  if (!upstream.ok) {
    const payload = await upstream.json().catch(() => ({}))
    return NextResponse.json(payload, { status: upstream.status, headers: guard })
  }

  return new NextResponse(await upstream.arrayBuffer(), {
    status: 200,
    headers: {
      ...guard,
      'Content-Type': 'application/pdf',
      'Content-Disposition':
        upstream.headers.get('content-disposition') ?? 'inline; filename="ai-diagnosis.pdf"',
    },
  })
}
