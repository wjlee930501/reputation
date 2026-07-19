import Link from 'next/link'
import Image from 'next/image'

import { ExternalIcon, PhoneIcon } from './icons'

interface Props {
  hospitalName: string
  hospitalRootUrl: string
  region: string[]
  specialties: string[]
  phone: string
  websiteUrl: string | null
  logoUrl?: string | null
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
  const specialtyLabel = specialties.filter(Boolean).join('·')
  const subline = region.join(' ').trim()
  const brandMeta = [specialtyLabel ? `${specialtyLabel} 진료` : '진료 안내', subline]
    .filter(Boolean)
    .join(' · ')
  const navItems = (
    <>
      <Link href={`${hospitalRootUrl}/treatments`}>진료 영역</Link>
      <Link href={`${hospitalRootUrl}/visit`}>진료시간·오시는 길</Link>
      <Link href={`${hospitalRootUrl}/doctor`}>의료진</Link>
      <Link href={`${hospitalRootUrl}/contents`}>건강 정보</Link>
      {websiteUrl && (
        <a href={websiteUrl} target="_blank" rel="noopener noreferrer" style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
          공식 홈페이지
          <ExternalIcon className="clinic-icon clinic-icon--sm" style={{ color: 'currentColor' }} />
        </a>
      )}
    </>
  )

  return (
    <header className="clinic-header">
      <div className="clinic-header-row">
        <Link href={hospitalRootUrl} className="clinic-header-brand" aria-label={`${hospitalName} 진료 안내 홈으로`}>
          {logoUrl ? (
            <Image
              src={logoUrl}
              alt={`${hospitalName} 로고`}
              width={160}
              height={48}
              className="clinic-header-brand-logo"
              unoptimized
            />
          ) : (
            <span className="clinic-header-brand-name">{hospitalName}</span>
          )}
          <span className="clinic-header-brand-meta">{brandMeta}</span>
        </Link>

        <nav className="clinic-header-nav" aria-label="병원 섹션">
          {navItems}
        </nav>

        <a className="clinic-header-cta" href={`tel:${phone}`}>
          <PhoneIcon className="clinic-icon clinic-icon--sm" style={{ color: 'currentColor' }} />
          <span>전화 상담</span>
          <strong>{phone}</strong>
        </a>
      </div>

      {/* 모바일 전용 가로 스크롤 nav. desktop에선 숨김. */}
      <nav className="clinic-header-nav-mobile" aria-label="병원 섹션 (모바일)">
        {navItems}
      </nav>
    </header>
  )
}
