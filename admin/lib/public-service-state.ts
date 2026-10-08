/**
 * 병원 공개 페이지가 지금 실제로 제공되는가 — 목록·헤더·패널·링크가 모두 이 함수 하나를 쓴다.
 *
 * 백엔드가 최소 공개 사실과 명시적 공개 권한을 함께 판정한다. 클라이언트는 kind와
 * blocker를 보존할 뿐 status/site_live를 다시 조합하지 않는다.
 */
export type PublicServiceState = 'live' | 'paused' | 'not_live'

export interface PublicServiceVerdict {
  kind: PublicServiceState
  remaining: readonly unknown[]
}

type PublicServiceSource = PublicServiceVerdict | {
  status?: unknown
  site_live?: unknown
  public_service_state?: PublicServiceVerdict | null
}

export function publicServiceState(source: PublicServiceSource | null | undefined): PublicServiceState {
  if (!source) return 'not_live'
  if ('kind' in source) return source.kind
  return source.public_service_state?.kind ?? 'not_live'
}

export function isPubliclyServing(source: PublicServiceSource | null | undefined): boolean {
  return publicServiceState(source) === 'live'
}
