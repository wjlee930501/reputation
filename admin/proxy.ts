import { NextRequest, NextResponse } from 'next/server'

import { adminAuthProxyConfig, buildAdminAuthProxyResponse } from './lib/auth-proxy'

// Next가 읽는 matcher는 이 `config` 리터럴 하나뿐이다. 예전에는 여기와 lib/auth-proxy.ts에
// 같은 matcher가 따로 선언돼 있었고, 테스트는 Next가 읽지 않는 쪽만 검증했다 — 한쪽만
// 바뀌면 인증 프록시가 조용히 다른 경로 집합에 적용될 수 있었다. re-export로 두 값을
// 하나로 묶어, 테스트가 검증하는 값이 런타임 값이 되게 한다.
export const config = adminAuthProxyConfig

export async function proxy(req: NextRequest) {
  return (await buildAdminAuthProxyResponse(req)) ?? NextResponse.next()
}
