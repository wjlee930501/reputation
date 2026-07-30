import { NextResponse } from 'next/server'
import { getApiBase } from '@/lib/config'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'

/** 진행 상태 조회. 토큰이 유일한 열쇠이므로 절대 캐시하지 않는다. */
export async function GET(_request: Request, { params }: { params: Promise<{ token: string }> }) {
  const { token } = await params
  const noStore = { 'Cache-Control': 'no-store', 'X-Robots-Tag': 'noindex, nofollow' }
  try {
    const upstream = await fetch(
      `${getApiBase()}/diagnosis/${encodeURIComponent(token)}/status`,
      { cache: 'no-store' },
    )
    const payload = await upstream.json().catch(() => ({}))
    return NextResponse.json(payload, { status: upstream.status, headers: noStore })
  } catch {
    return NextResponse.json({ error: '상태를 불러오지 못했습니다.' }, { status: 502, headers: noStore })
  }
}
