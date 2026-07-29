import { NextResponse } from 'next/server'
import { getApiBase } from '@/lib/config'

export const runtime = 'nodejs'
// 남은 자리는 실시간 값이다. 캐시되면 마감된 뒤에도 "자리 있음"으로 보인다.
export const dynamic = 'force-dynamic'

export async function GET() {
  try {
    const upstream = await fetch(`${getApiBase()}/public/diagnosis/slots`, { cache: 'no-store' })
    if (!upstream.ok) {
      return NextResponse.json({ ok: false }, { status: 502, headers: { 'Cache-Control': 'no-store' } })
    }
    return NextResponse.json(await upstream.json(), {
      headers: { 'Cache-Control': 'no-store' },
    })
  } catch {
    // 카운터를 못 읽으면 **표시하지 않는다.** 추측값을 보여주면 실제 카운터라는
    // 약속이 깨지고, 마감인데 "자리 있음"으로 보이는 것이 가장 나쁘다.
    return NextResponse.json({ ok: false }, { status: 502, headers: { 'Cache-Control': 'no-store' } })
  }
}
