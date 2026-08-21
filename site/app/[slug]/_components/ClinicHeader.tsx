import Link from 'next/link'

import { displayClinicLabels } from '@/lib/clinic-design'

import { CalendarIcon, ClockIcon, MapPinIcon, PhoneIcon } from './icons'
import { ClinicHeaderNavigation } from './ClinicHeaderNavigation'
import { ClinicHeaderLogo } from './ClinicHeaderLogo'

interface Props {
  readonly hospitalName: string
  readonly hospitalRootUrl: string
  readonly region: string[]
  readonly specialties: string[]
  readonly phone: string
  readonly websiteUrl: string | null
  readonly logoUrl?: string | null
}

export function ClinicHeader({
  hospitalName,
  hospitalRootUrl,
  region,
  specialties,
  phone,
  websiteUrl,
  logoUrl,
}: Props) {
  // 진료과 표기는 프로파일의 specialties[]에서만 파생 — 하드코딩 금지.
  const specialtyLabel = displayClinicLabels(specialties, 3).join('·')
  const subline = displayClinicLabels(region).join(' ')
  const brandMeta = [specialtyLabel ? `${specialtyLabel} 진료` : '진료 안내', subline]
    .filter(Boolean)
    .join(' · ')
  return (
    <header className="clinic-header">
      <div className="clinic-header-row">
        <Link href={hospitalRootUrl} className="clinic-header-brand" aria-label={`${hospitalName} 진료 안내 홈으로`}>
          <ClinicHeaderLogo hospitalName={hospitalName} logoUrl={logoUrl} />
          <span className="clinic-header-brand-meta">{brandMeta}</span>
        </Link>

        <ClinicHeaderNavigation hospitalRootUrl={hospitalRootUrl} websiteUrl={websiteUrl} variant="desktop" />

        <a className="clinic-header-cta" href={`tel:${phone}`}>
          <PhoneIcon className="clinic-icon clinic-icon--sm" style={{ color: 'currentColor' }} />
          <span>전화 상담</span>
          <strong>{phone}</strong>
        </a>
      </div>

      <ClinicHeaderNavigation hospitalRootUrl={hospitalRootUrl} websiteUrl={websiteUrl} variant="mobile" />

      <nav className="clinic-mobile-actionbar" aria-label="빠른 병원 문의">
        <a href={`tel:${phone}`}>
          <PhoneIcon className="clinic-icon" />
          전화
        </a>
        <Link href={`${hospitalRootUrl}/visit`}>
          <ClockIcon className="clinic-icon" />
          진료시간
        </Link>
        <Link href={`${hospitalRootUrl}/treatments`}>
          <CalendarIcon className="clinic-icon" />
          진료안내
        </Link>
        <Link href={`${hospitalRootUrl}/visit`}>
          <MapPinIcon className="clinic-icon" />
          길찾기
        </Link>
      </nav>
    </header>
  )
}
