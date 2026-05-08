import Link from 'next/link'

import { ExternalIcon, PhoneIcon } from './icons'

interface Props {
  hospitalName: string
  hospitalSlug: string
  region: string[]
  specialties: string[]
  phone: string
  websiteUrl: string | null
}

export function ClinicHeader({
  hospitalName,
  hospitalSlug,
  region,
  specialties,
  phone,
  websiteUrl,
}: Props) {
  const subline = `${region.join(' ')} ${specialties.join(' · ')}`.trim()
  const navItems = (
    <>
      <Link href={`/${hospitalSlug}/contents`}>의료 콘텐츠</Link>
      <Link href={`/${hospitalSlug}/doctor`}>의료진</Link>
      <Link href={`/${hospitalSlug}/treatments`}>진료 영역</Link>
      <Link href={`/${hospitalSlug}/visit`}>진료 안내</Link>
      {websiteUrl && (
        <a href={websiteUrl} target="_blank" rel="noopener" style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
          공식 홈페이지
          <ExternalIcon className="clinic-icon clinic-icon--sm" style={{ color: 'currentColor' }} />
        </a>
      )}
    </>
  )

  return (
    <header className="clinic-header">
      <div className="clinic-header-row">
        <Link href={`/${hospitalSlug}`} className="clinic-header-brand" aria-label={`${hospitalName} 콘텐츠 허브 홈으로`}>
          <span className="clinic-header-brand-name">{hospitalName}</span>
          <span className="clinic-header-brand-meta">
            의료 콘텐츠 허브{subline && ` · ${subline}`}
          </span>
        </Link>

        <nav className="clinic-header-nav" aria-label="콘텐츠 허브 섹션">
          {navItems}
        </nav>

        <a className="clinic-header-cta" href={`tel:${phone}`}>
          <PhoneIcon className="clinic-icon clinic-icon--sm" style={{ color: 'currentColor' }} />
          전화 문의
        </a>
      </div>

      {/* 모바일 전용 가로 스크롤 nav. desktop에선 숨김. */}
      <nav className="clinic-header-nav-mobile" aria-label="콘텐츠 허브 섹션 (모바일)">
        {navItems}
      </nav>
    </header>
  )
}
