import { NextResponse } from 'next/server'

// IndexNow 키 파일 — 제출하는 URL과 **같은 호스트**에서 응답되어야 소유가 증명된다.
// 병원 커스텀 도메인(jangclinic.kr 등)과 플랫폼 호스트 양쪽에서 같은 값을 돌려준다.
//
// 경로에 확장자(.txt)가 있어 host-routing의 isReservedPath가 정적 자산으로 판정하므로
// /{slug}/indexnow-key.txt로 rewrite되지 않는다 — 예약 목록에 따로 넣을 필요가 없다.
//
// 값이 없으면 404를 준다. 빈 문자열을 200으로 돌려주면 IndexNow가 키 불일치가 아니라
// "키 파일은 있는데 값이 다르다"로 처리해 원인 파악이 어려워진다.
export const dynamic = 'force-dynamic'

export function GET() {
  const key = process.env.INDEXNOW_KEY?.trim()
  if (!key) {
    return new NextResponse('Not Found', { status: 404 })
  }
  return new NextResponse(key, {
    status: 200,
    headers: {
      'Content-Type': 'text/plain; charset=utf-8',
      'Cache-Control': 'public, max-age=3600',
    },
  })
}
