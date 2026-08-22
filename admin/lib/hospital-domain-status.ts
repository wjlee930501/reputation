import { platformSiteHost } from './platform-domain.ts'

type DomainTone = 'live' | 'waiting' | 'dns_verified' | 'issuing' | 'failed' | 'default' | 'empty'

interface HospitalDomainInput {
  name?: string | null
  slug?: string | null
  aeo_domain?: string | null
  site_built?: boolean | null
  site_live?: boolean | null
  domain_cert_dns_verified_at?: string | null
  domain_cert_job_state?: string | null
  domain_last_checked_at?: string | null
  domain_last_check_ok?: boolean | null
}

export interface HospitalDomainStatus {
  label: '운영 중' | '공개 주소 확인 대기' | 'DNS 확인 완료' | '인증서 발급 중' | '인증서 실패' | '기본 주소' | '미설정'
  detail: string
  tone: DomainTone
}

function normalizedDomain(value: string | null | undefined): string {
  return value?.trim().toLowerCase() ?? ''
}

/**
 * 마지막 실제 확인 시각을 사람이 읽는 문구로.
 *
 * 배지가 언제의 사실을 말하는지 밝히지 않으면, 실제 주소가 열리는데도 "확인 대기"로
 * 남아 있던 화면과 똑같이 신뢰할 수 없는 표시가 된다.
 */
export function domainLastCheckedLabel(
  checkedAt: string | null | undefined,
  ok?: boolean | null,
): string | null {
  if (!checkedAt) return null
  const parsed = new Date(checkedAt)
  if (Number.isNaN(parsed.getTime())) return null
  const stamp = parsed.toLocaleString('ko-KR', {
    month: 'numeric',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
  if (ok === true) return `마지막 확인 ${stamp} · 응답 정상`
  if (ok === false) return `마지막 확인 ${stamp} · 응답 실패`
  return `마지막 확인 ${stamp}`
}

function withLastChecked(hospital: HospitalDomainInput, base: string): string {
  const suffix = domainLastCheckedLabel(hospital.domain_last_checked_at, hospital.domain_last_check_ok)
  return suffix ? `${base} · ${suffix}` : base
}

/**
 * 마지막 관측만으로 "운영 중"이라고 말해도 되는지.
 *
 * 관측이 정상이어도 인증서 작업이 발급 중이거나 실패 상태면 그 사실을 가려서는 안 된다.
 * 도메인이 지금 응답한다는 것과 인증서 발급이 끝났다는 것은 다른 사실이고, 운영자는
 * 후자를 보고 손을 써야 한다 — 발급 중 배지가 사라지면 지연이나 실패를 알 방법이 없다.
 *
 * 그래서 관측은 인증서 상태가 비어 있거나(도메인 재저장으로 초기화된 경우) 이미
 * DONE일 때만 '운영 중'의 근거가 된다. 목록·헤더·패널이 모두 이 함수를 쓴다.
 */
function liveCheckProvesServing(input: {
  domain_cert_job_state?: string | null
  domain_last_check_ok?: boolean | null
}): boolean {
  if (input.domain_last_check_ok !== true) return false
  const certState = input.domain_cert_job_state
  return !certState || certState === 'DONE'
}

export function readHospitalDomainStatus(hospital: HospitalDomainInput): HospitalDomainStatus {
  const domain = normalizedDomain(hospital.aeo_domain)
  
  // DM-U3: 커스텀 도메인이 설정된 경우, site_live가 아닌 DNS/cert 상태로 판단
  if (domain) {
    const certState = hospital.domain_cert_job_state
    const dnsVerified = !!hospital.domain_cert_dns_verified_at

    // 진행 중이거나 실패한 인증서 작업이 먼저다. 마지막 관측이 정상이더라도 그 사실을
    // 가리면 운영자가 발급 지연·실패를 알아챌 화면이 사라진다.
    if (certState === 'ISSUING') {
      return {
        label: '인증서 발급 중',
        detail: withLastChecked(hospital, domain),
        tone: 'issuing',
      }
    }

    if (certState === 'FAILED') {
      return {
        label: '인증서 실패',
        detail: withLastChecked(hospital, domain),
        tone: 'failed',
      }
    }

    if (certState === 'DONE') {
      return {
        label: '운영 중',
        detail: withLastChecked(hospital, domain),
        tone: 'live',
      }
    }

    // A-1: 인증서 상태가 비어 있어도(도메인 재저장으로 초기화) 주소가 실제로 응답하면
    // 살아 있는 주소를 두고 '확인 대기'로 남기지 않는다.
    if (liveCheckProvesServing(hospital)) {
      return {
        label: '운영 중',
        detail: withLastChecked(hospital, domain),
        tone: 'live',
      }
    }

    if (dnsVerified) {
      return {
        label: 'DNS 확인 완료',
        detail: withLastChecked(hospital, domain),
        tone: 'dns_verified',
      }
    }
    
    // 저장됨, DNS 미검증
    return {
      label: '공개 주소 확인 대기',
      detail: withLastChecked(hospital, domain),
      tone: 'waiting',
    }
  }

  // 커스텀 도메인 없음: 기본 주소 또는 미설정 (site_live/site_built 사용)
  if (hospital.site_built || hospital.site_live) {
    const slug = normalizedDomain(hospital.slug) || 'slug'
    return {
      label: '기본 주소',
      detail: `${slug}.${platformSiteHost()}`,
      tone: 'default',
    }
  }

  return {
    label: '미설정',
    detail: '병원 기본 정보에서 공개 주소 연결',
    tone: 'empty',
  }
}

export type CustomDomainPanelStatus =
  | 'live'
  | 'waiting'
  | 'dns_verified'
  | 'issuing'
  | 'failed'
  | 'unsaved'
  | 'ready'
  | 'empty'

/**
 * `/profile`의 자기 도메인 패널 배지 상태.
 *
 * 목록·헤더와 같은 규칙을 쓴다 — 세 화면이 서로 다른 결론을 내면 운영자는 어느 쪽도
 * 믿지 않게 된다.
 */
export function customDomainPanelStatus(input: {
  hasUnsavedChange: boolean
  domainSaved: boolean
  activationReady: boolean
  domain_cert_job_state?: string | null
  domain_cert_dns_verified_at?: string | null
  domain_last_check_ok?: boolean | null
}): CustomDomainPanelStatus {
  if (input.hasUnsavedChange) return 'unsaved'
  if (!input.domainSaved) return input.activationReady ? 'ready' : 'empty'
  if (input.domain_cert_job_state === 'FAILED') return 'failed'
  if (input.domain_cert_job_state === 'ISSUING') return 'issuing'
  if (input.domain_cert_job_state === 'DONE') return 'live'
  if (liveCheckProvesServing(input)) return 'live'
  if (input.domain_cert_dns_verified_at) return 'dns_verified'
  return 'waiting'
}

export function domainSearchText(hospital: HospitalDomainInput): string {
  const status = readHospitalDomainStatus(hospital)
  return [
    hospital.name,
    hospital.slug,
    hospital.aeo_domain,
    status.label,
    status.detail,
  ]
    .filter((value): value is string => typeof value === 'string' && value.length > 0)
    .join(' ')
    .toLowerCase()
}

// 백엔드가 ISSUING 클레임을 만료로 보는 시간(CERTIFICATE_LEASE_MINUTES)과 같아야 한다.
// 이 시간이 지나면 재확인 요청이 새 발급 작업을 잡으므로 버튼을 막아 둘 이유가 없다.
export const CERTIFICATE_LEASE_MINUTES = 30

export function certificateIssuingElapsedMinutes(
  startedAt: string | null | undefined,
  now: number = Date.now(),
): number | null {
  if (!startedAt) return null
  const started = new Date(startedAt).getTime()
  if (Number.isNaN(started)) return null
  return Math.max(0, Math.floor((now - started) / 60000))
}

/**
 * 진행 중이라고 표시된 인증서 작업을 지금 다시 걸 수 있는지.
 *
 * 발급 작업이 커밋된 직후 워커가 죽으면 아무도 폴링하지 않는 ISSUING이 남는다.
 * 버튼을 계속 잠가 두면 운영자가 도메인을 되살릴 방법이 없다.
 */
export function certificateIssuingCanBeRetried(
  startedAt: string | null | undefined,
  now: number = Date.now(),
): boolean {
  const elapsed = certificateIssuingElapsedMinutes(startedAt, now)
  if (elapsed === null) return true
  return elapsed >= CERTIFICATE_LEASE_MINUTES
}

interface DomainHeaderInput {
  site_live?: boolean
  aeo_domain?: string | null
  domain_cert_dns_verified_at?: string | null
  domain_cert_job_state?: string | null
  domain_last_check_ok?: boolean | null
}

/** 헤더 점 색상은 배지 문구와 같은 판단을 써야 한다 (녹색 점 + '미확인' 조합 방지). */
export function domainHeaderIsLive(profile: DomainHeaderInput): boolean {
  return domainHeaderStatus(profile) === '운영 중'
}

export function domainHeaderStatus(profile: DomainHeaderInput) {
  // DM-U3 #4: 커스텀 도메인 행은 DNS/cert 상태로 판단. site_live는 기본 URL 상태.
  if (!profile.aeo_domain) {
    // 커스텀 도메인이 없으면 site_live로 기본 주소 상태만 표시
    return profile.site_live ? '운영 중' : '공개 주소 확인 대기'
  }

  // 커스텀 도메인이 있으면 DNS/cert 상태로 판단. 발급 중·실패는 마지막 관측이 정상이어도
  // 그대로 드러낸다 — 그 배지가 사라지면 운영자가 인증서 지연·실패를 알 방법이 없다.
  if (profile.domain_cert_job_state === 'ISSUING') return 'DNS 확인 완료 · 인증서 발급 중'
  if (profile.domain_cert_job_state === 'FAILED') return 'DNS 확인 완료 · 인증서 실패'
  if (profile.domain_cert_job_state === 'DONE') return '운영 중'
  // A-1: 인증서 상태가 비어 있을 때만 관측이 '운영 중'의 근거가 된다.
  if (liveCheckProvesServing(profile)) return '운영 중'
  if (profile.domain_cert_dns_verified_at) return 'DNS 확인 완료'
  return '저장됨 · DNS 미확인'
}
