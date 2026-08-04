import { NextRequest, NextResponse } from 'next/server.js'

import { readSessionToken } from './session.ts'

/** 현재 로그인한 운영자의 신원. 화면이 권한 전용 조작을 보여줄지 판단하는 근거다.
 *
 * 브라우저 저장소(sessionStorage) 대신 서명된 세션 쿠키를 읽는다 — 저장소 방식은
 * 새 탭이나 배포 이전부터 살아 있던 세션에서 값이 비어, "모르면 보여준다"로 처리하면
 * 권한 없는 운영자에게 소유자 전용 버튼이 노출된다(서버는 403으로 막지만 UI가 거짓말한다).
 *
 * role은 로그인 시점 값이라 승격·강등 직후에는 재로그인 전까지 낡을 수 있다. 그래서
 * 권한 판정의 정본은 언제나 백엔드이고, 이 값은 표시용이다.
 */
export async function handleAdminSessionRead(req: NextRequest): Promise<NextResponse> {
  const secret = process.env.ADMIN_SESSION_SECRET
  const token = req.cookies.get('admin_session')?.value
  const session = secret && token ? await readSessionToken(token, secret) : null

  const res = session
    ? NextResponse.json({
        accountId: session.accountId,
        email: session.email,
        name: session.name,
        role: session.role,
      })
    : NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
  res.headers.set('cache-control', 'no-store, private')
  return res
}
