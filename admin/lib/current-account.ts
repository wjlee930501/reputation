export type CurrentAccount = {
  accountId: string
  email: string
  name: string
  role: string
}

/** 현재 로그인한 운영자를 서버(서명된 세션 쿠키)에서 읽는다.
 *
 * 실패하면 null. 화면은 null을 **권한 없음**으로 다뤄야 한다 — "모르면 보여준다"로
 * 처리하면 새 탭이나 오래된 세션에서 소유자 전용 버튼이 그대로 노출된다.
 * 권한 판정의 정본은 백엔드이며, 이 값은 화면 표시용이다.
 */
export async function fetchCurrentAccount(): Promise<CurrentAccount | null> {
  try {
    const res = await fetch('/api/auth/session', { cache: 'no-store' })
    if (!res.ok) return null
    const data: unknown = await res.json()
    if (typeof data !== 'object' || data === null) return null
    const { accountId, email, name, role } = data as Record<string, unknown>
    if (
      typeof accountId !== 'string' ||
      typeof email !== 'string' ||
      typeof name !== 'string' ||
      typeof role !== 'string'
    ) {
      return null
    }
    return { accountId, email, name, role }
  } catch {
    return null
  }
}
