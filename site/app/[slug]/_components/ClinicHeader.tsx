import Link from 'next/link'
import Image from 'next/image'

import { VISIT_HOURS_ANCHOR } from '@/lib/business-hours'

import { CalendarIcon, ClockIcon, ExternalIcon, MapPinIcon, PhoneIcon } from './icons'

/** 헤더·모바일 액션바가 "지금 보고 있는 페이지"를 아는 단위. */
export type ClinicSection = 'home' | 'treatments' | 'visit' | 'doctor' | 'contents'

interface Props {
  hospitalName: string
  hospitalRootUrl: string
  region: string[]
  specialties: string[]
  phone: string
  websiteUrl: string | null
  logoUrl?: string | null
  /** 현재 페이지. 이 값이 없으면 모든 항목이 이동 가능한 링크로 남는다. */
  currentSection?: ClinicSection
  /** 있으면 '길찾기'가 지도로 나간다. 없으면 오시는 길 안내로 보낸다. */
  googleMapsUrl?: string | null
}

/**
 * 한 줄 헤더: 병원명 | 섹션 이동 | 전화. 병원명은 어느 폭에서도 첫 화면에 보인다.
 * 720px 아래에서는 섹션 이동이 브랜드 줄 아래 가로 스크롤 한 줄로 내려가고,
 * 화면 하단에 전화·진료시간·진료안내·길찾기 행동 바가 붙는다.
 *
 * `region`·`specialties`는 헤더에 그리지 않는다 — 첫 화면의 kicker가 같은 사실을
 * 말하므로 여기서 반복하면 첫 화면 위쪽이 두 줄로 무거워진다. props는 호출부 계약을
 * 위해 남긴다.
 */
export function ClinicHeader({
  hospitalName,
  hospitalRootUrl,
  phone,
  websiteUrl,
  logoUrl,
  currentSection,
  googleMapsUrl = null,
}: Props) {
  /**
   * 지금 보고 있는 페이지를 다시 여는 링크는 환자에게 아무것도 주지 않는다 — 눌러도
   * 아무 일이 없고, 스크린 리더에는 현재 위치가 드러나지 않는다(S-1). 현재 항목은
   * 링크에서 빼고 `aria-current`로 위치를 알린다.
   */
  function navLink(section: ClinicSection, href: string, label: string) {
    if (currentSection === section) {
      return <span aria-current="page">{label}</span>
    }
    return <Link href={href}>{label}</Link>
  }

  const navItems = (
    <>
      {navLink('treatments', `${hospitalRootUrl}/treatments`, '진료 영역')}
      {navLink('doctor', `${hospitalRootUrl}/doctor`, '의료진')}
      {navLink('contents', `${hospitalRootUrl}/contents`, '건강 정보')}
      {navLink('visit', `${hospitalRootUrl}/visit`, '진료시간·오시는 길')}
      {websiteUrl && (
        <a href={websiteUrl} target="_blank" rel="noopener noreferrer">
          공식 홈페이지
          <ExternalIcon className="hub-icon hub-icon--sm" style={{ color: 'currentColor' }} />
        </a>
      )}
    </>
  )

  return (
    <header className="hub-header">
      <div className="hub-container">
        <div className="hub-header-row">
          <Link href={hospitalRootUrl} className="hub-header-brand" aria-label={`${hospitalName} 진료 안내 홈으로`}>
            {logoUrl ? (
              <Image
                src={logoUrl}
                alt={`${hospitalName} 로고`}
                width={160}
                height={48}
                className="hub-header-brand-logo"
                unoptimized
              />
            ) : (
              hospitalName
            )}
          </Link>

          <nav className="hub-header-nav" aria-label="병원 섹션">
            {navItems}
          </nav>

          <a className="hub-header-cta" href={`tel:${phone}`}>
            <PhoneIcon className="hub-icon hub-icon--sm" />
            <span>전화 상담</span>
            <strong>{phone}</strong>
          </a>
        </div>

        {/* 모바일 전용 가로 스크롤 nav. desktop에선 숨김. */}
        <nav className="hub-header-subnav" aria-label="병원 섹션 (모바일)">
          {navItems}
        </nav>
      </div>

      <nav className="hub-actionbar" aria-label="빠른 병원 문의">
        <a href={`tel:${phone}`}>
          <PhoneIcon className="hub-icon" />
          전화
        </a>
        {/* /visit에서는 이 두 항목이 같은 페이지를 다시 열 뿐이었다. 진료시간은 이
            페이지의 표로 내려가고, 길찾기는 라벨이 약속한 대로 지도로 나간다(S-1). */}
        {currentSection === 'visit' ? (
          <a href={`#${VISIT_HOURS_ANCHOR}`}>
            <ClockIcon className="hub-icon" />
            진료시간
          </a>
        ) : (
          <Link href={`${hospitalRootUrl}/visit#${VISIT_HOURS_ANCHOR}`}>
            <ClockIcon className="hub-icon" />
            진료시간
          </Link>
        )}
        {currentSection === 'treatments' ? (
          <span aria-current="page">
            <CalendarIcon className="hub-icon" />
            진료안내
          </span>
        ) : (
          <Link href={`${hospitalRootUrl}/treatments`}>
            <CalendarIcon className="hub-icon" />
            진료안내
          </Link>
        )}
        {googleMapsUrl ? (
          <a href={googleMapsUrl} target="_blank" rel="noopener noreferrer">
            <MapPinIcon className="hub-icon" />
            길찾기
          </a>
        ) : (
          <Link href={`${hospitalRootUrl}/visit#${VISIT_HOURS_ANCHOR}`}>
            <MapPinIcon className="hub-icon" />
            오시는 길
          </Link>
        )}
      </nav>
    </header>
  )
}
