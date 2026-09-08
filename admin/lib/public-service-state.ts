/**
 * 병원 공개 페이지가 지금 실제로 제공되는가 — 목록·헤더·패널·링크가 모두 이 함수 하나를 쓴다.
 *
 * 백엔드 `_has_public_site`(api/admin/hospitals.py) 및 공개 게이트(api/public/site.py)와
 * 같은 판정: status === 'ACTIVE' && site_live. `site_live`만 보면 안 된다 — 일시정지는
 * status만 PAUSED로 바꾸고 site_live는 그대로 두므로, site_live만 읽는 화면은 정지된
 * 병원을 "공개 중"이라고 말한다.
 */
import type { HospitalStatusValue } from '../types/index.ts'

export type PublicServiceState = 'live' | 'paused' | 'not_live'

export interface PublicServiceInput {
  status?: HospitalStatusValue | null
  site_live?: boolean | null
}

export function publicServiceState(hospital: PublicServiceInput | null | undefined): PublicServiceState {
  if (!hospital) return 'not_live'
  if (hospital.status === 'PAUSED') return 'paused'
  if (hospital.status === 'ACTIVE' && hospital.site_live === true) return 'live'
  return 'not_live'
}

export function isPubliclyServing(hospital: PublicServiceInput | null | undefined): boolean {
  return publicServiceState(hospital) === 'live'
}
