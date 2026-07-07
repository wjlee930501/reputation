import { platformSiteHost } from './platform-domain.ts'

type DomainTone = 'live' | 'waiting' | 'default' | 'empty'

interface HospitalDomainInput {
  name?: string | null
  slug?: string | null
  aeo_domain?: string | null
  site_built?: boolean | null
  site_live?: boolean | null
}

export interface HospitalDomainStatus {
  label: '운영중' | 'DNS 대기' | '기본 주소' | '미설정'
  detail: string
  tone: DomainTone
}

function normalizedDomain(value: string | null | undefined): string {
  return value?.trim().toLowerCase() ?? ''
}

export function readHospitalDomainStatus(hospital: HospitalDomainInput): HospitalDomainStatus {
  const domain = normalizedDomain(hospital.aeo_domain)
  if (domain) {
    return {
      label: hospital.site_live ? '운영중' : 'DNS 대기',
      detail: domain,
      tone: hospital.site_live ? 'live' : 'waiting',
    }
  }

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
    detail: '프로파일에서 도메인 연결',
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
