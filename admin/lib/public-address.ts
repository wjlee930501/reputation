/**
 * 병원 공개 주소 — 지금 실제로 열리는 주소만 링크로 만든다.
 *
 * 머리글·병원 목록·콘텐츠 「공개 글」·도메인 패널이 모두 이 함수 하나를 쓴다(2026-11-20
 * 마케팅 요청: 모든 주소 링크 + 복사). 판정은 화면이 다시 조합하지 않는다:
 * - 공개 서비스 중인가 = 서버 판정 `public_service_state`(`isPubliclyServing`).
 * - 자기 도메인이 열리는가 = 인증서 발급 완료 또는 마지막 확인이 정상 응답
 *   (`liveCheckProvesServing`, 도메인 상태 배지와 같은 규칙).
 * 자기 도메인을 저장만 하고 아직 DNS·인증서 전이면 그 도메인으로 링크를 만들지 않는다 —
 * 운영자가 누르면 열리지 않는 주소다(콘텐츠 상세 링크가 그랬다).
 */

import { liveCheckProvesServing } from './hospital-domain-status.ts'
import { platformSubdomainHost } from './platform-domain.ts'
import { isPubliclyServing, type PublicServiceVerdict } from './public-service-state.ts'
import type { HospitalStatusValue } from '../types/index.ts'

export interface PublicAddressInput {
  slug?: string | null
  aeo_domain?: string | null
  status?: HospitalStatusValue | null
  site_live?: boolean | null
  public_service_state?: PublicServiceVerdict | null
  domain_cert_job_state?: string | null
  domain_last_check_ok?: boolean | null
}

export interface PublicAddress {
  /** `https://` 붙은 주소 */
  url: string
  /** 화면에 보이는 호스트 */
  host: string
  /** 지금 열리는가 — false면 글자로만 보이고 링크·「공개 글」을 만들지 않는다 */
  open: boolean
}

export interface PublicAddresses {
  /** 운영 주소: 열리는 자기 도메인, 없으면 열리는 기본 주소. 공개 전이면 null */
  live: PublicAddress | null
  /** 기본 주소(`{slug}.reputation…`) */
  platform: PublicAddress | null
  /** 자기 도메인 */
  custom: PublicAddress | null
}

function normalizedHost(value: string | null | undefined): string {
  return (value ?? '').trim().toLowerCase().replace(/^https?:\/\//, '').replace(/\/.*$/, '')
}

export function readPublicAddresses(hospital: PublicAddressInput): PublicAddresses {
  const serving = isPubliclyServing(hospital)
  const platformHost = platformSubdomainHost(hospital.slug)
  const platform = platformHost
    ? { url: `https://${platformHost}`, host: platformHost, open: serving }
    : null
  const customHost = normalizedHost(hospital.aeo_domain)
  const custom = customHost
    ? { url: `https://${customHost}`, host: customHost, open: serving && (hospital.domain_cert_job_state === 'DONE' || liveCheckProvesServing(hospital)) }
    : null
  const live = custom?.open ? custom : platform?.open ? platform : null
  return { live, platform, custom }
}

/** 공개 글 주소. 병원이 지금 열리지 않으면 null — 버튼을 만들지 않는다. */
export function publicContentUrl(hospital: PublicAddressInput, contentId: string): string | null {
  const live = readPublicAddresses(hospital).live
  if (!live || !contentId) return null
  return `${live.url}/contents/${encodeURIComponent(contentId)}`
}
