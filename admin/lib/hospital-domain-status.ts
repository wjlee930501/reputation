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
}

export interface HospitalDomainStatus {
  label: '운영 중' | '공개 주소 확인 대기' | 'DNS 확인 완료' | '인증서 발급 중' | '인증서 실패' | '기본 주소' | '미설정'
  detail: string
  tone: DomainTone
}

function normalizedDomain(value: string | null | undefined): string {
  return value?.trim().toLowerCase() ?? ''
}

export function readHospitalDomainStatus(hospital: HospitalDomainInput): HospitalDomainStatus {
  const domain = normalizedDomain(hospital.aeo_domain)
  
  // DM-U3: 커스텀 도메인이 설정된 경우, site_live가 아닌 DNS/cert 상태로 판단
  if (domain) {
    const certState = hospital.domain_cert_job_state
    const dnsVerified = !!hospital.domain_cert_dns_verified_at
    
    if (certState === 'DONE') {
      return {
        label: '운영 중',
        detail: domain,
        tone: 'live',
      }
    }
    
    if (certState === 'ISSUING') {
      return {
        label: '인증서 발급 중',
        detail: domain,
        tone: 'issuing',
      }
    }
    
    if (certState === 'FAILED') {
      return {
        label: '인증서 실패',
        detail: domain,
        tone: 'failed',
      }
    }
    
    if (dnsVerified) {
      return {
        label: 'DNS 확인 완료',
        detail: domain,
        tone: 'dns_verified',
      }
    }
    
    // 저장됨, DNS 미검증
    return {
      label: '공개 주소 확인 대기',
      detail: domain,
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

export function domainHeaderStatus(profile: {
  site_live?: boolean
  aeo_domain?: string | null
  domain_cert_dns_verified_at?: string | null
  domain_cert_job_state?: string | null
}) {
  // DM-U3 #4: 커스텀 도메인 행은 DNS/cert 상태로 판단. site_live는 기본 URL 상태.
  if (!profile.aeo_domain) {
    // 커스텀 도메인이 없으면 site_live로 기본 주소 상태만 표시
    return profile.site_live ? '운영 중' : '공개 주소 확인 대기'
  }
  
  // 커스텀 도메인이 있으면 DNS/cert 상태로 판단
  if (profile.domain_cert_job_state === 'DONE') return '운영 중'
  if (profile.domain_cert_job_state === 'ISSUING') return 'DNS 확인 완료 · 인증서 발급 중'
  if (profile.domain_cert_job_state === 'FAILED') return 'DNS 확인 완료 · 인증서 실패'
  if (profile.domain_cert_dns_verified_at) return 'DNS 확인 완료'
  return '저장됨 · DNS 미확인'
}
