import { NextRequest, NextResponse } from 'next/server'

import { buildAdminAuthProxyResponse } from './lib/auth-proxy'

// ⚠️ 이 리터럴은 lib/auth-proxy.ts의 `adminAuthProxyConfig`와 **항상 같아야 한다.**
//
// 왜 복제하는가: Next(Turbopack)는 라우트 파일의 `config`를 컴파일 시점에 정적으로
// 파싱하므로 `export const config = adminAuthProxyConfig` 같은 re-export를 거부한다
// (빌드 실패). 예전에 re-export로 묶으려던 시도가 admin 빌드를 깨뜨린 원인이었다.
//
// 그래서 값을 두 곳에 두되, **둘이 어긋나면 실패하는 테스트**로 묶는다
// (lib/admin-proxy-route-wiring.test.ts). 한쪽만 바꾸면 CI가 잡는다 — 예전처럼
// 테스트가 Next가 읽지 않는 값만 검증하는 상태로는 돌아가지 않는다.
export const config = {
  matcher: ['/((?!_next/|favicon\\.ico$|robots\\.txt$|sitemap\\.xml$).*)'],
}

export async function proxy(req: NextRequest) {
  return (await buildAdminAuthProxyResponse(req)) ?? NextResponse.next()
}
