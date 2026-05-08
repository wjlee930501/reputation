import Link from 'next/link'

import { PhoneIcon } from './icons'

interface Props {
  hospitalName: string
  hospitalSlug: string
  region: string[]
  specialties: string[]
  phone: string
}

export function ClinicHeader({ hospitalName, hospitalSlug, region, specialties, phone }: Props) {
  const subline = [region.join(' '), specialties.join(' · ')].filter(Boolean).join(' / ')
  return (
    <header className="clinic-header">
      <Link href={`/${hospitalSlug}`} className="clinic-header-brand" aria-label={`${hospitalName} 홈으로`}>
        <span className="clinic-header-brand-name">{hospitalName}</span>
        {subline && <span className="clinic-header-brand-meta">{subline}</span>}
      </Link>

      <nav className="clinic-header-nav" aria-label="병원 페이지 섹션">
        <a href="#doctor">원장 소개</a>
        <a href="#treatments">진료 분야</a>
        <Link href={`/${hospitalSlug}/contents`}>의료 정보</Link>
        <a href="#contact">진료 안내</a>
      </nav>

      <a className="clinic-header-cta" href={`tel:${phone}`}>
        <PhoneIcon className="clinic-icon clinic-icon--sm" style={{ color: 'currentColor' }} />
        전화 문의
      </a>
    </header>
  )
}
