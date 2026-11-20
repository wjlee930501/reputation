import Link from 'next/link'
import { Fragment } from 'react'
import Image from 'next/image'

import { VISIT_HOURS_ANCHOR } from '@/lib/business-hours'
import { displayClinicLabels } from '@/lib/clinic-design'
import {
  DEFAULT_MOBILE_BAR,
  DEFAULT_NAV_ITEMS,
  type ClinicSiteConfig,
  type SiteMobileBarItem,
  type SiteNavItem,
} from '@/lib/clinic-site-config'

import { CalendarIcon, ClockIcon, ExternalIcon, MapPinIcon, PhoneIcon } from './icons'
import { regionSentenceLabel } from '@/lib/clinic-region'

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
  /**
   * 공개된 섹션 설정 — 메뉴 항목·순서와 모바일 하단 바. 없으면 지금 메뉴 그대로다. 메뉴는 하위 페이지로
   * 가므로(홈 섹션을 꺼도 하위 페이지는 남는다) 모든 병원 페이지가 같은 값을 넘긴다.
   */
  siteConfig?: ClinicSiteConfig | null
}

export function ClinicHeader({
  hospitalName,
  hospitalRootUrl,
  region,
  specialties,
  phone,
  websiteUrl,
  logoUrl,
  currentSection,
  googleMapsUrl = null,
  siteConfig = null,
}: Props) {
  const menu: readonly SiteNavItem[] = siteConfig?.nav.items ?? DEFAULT_NAV_ITEMS
  const bar: readonly SiteMobileBarItem[] = siteConfig?.nav.mobileBar ?? DEFAULT_MOBILE_BAR
  // 진료과 표기는 프로파일의 specialties[]에서만 파생 — 하드코딩 금지.
  const specialtyLabel = displayClinicLabels(specialties).join('·')
  const subline = regionSentenceLabel(region)
  const brandMeta = [specialtyLabel ? `${specialtyLabel} 진료` : '진료 안내', subline]
    .filter(Boolean)
    .join(' · ')
  /**
   * 지금 보고 있는 페이지를 다시 여는 링크는 환자에게 아무것도 주지 않는다 — 눌러도
   * 아무 일이 없고, 스크린 리더에는 현재 위치가 드러나지 않는다(S-1). 현재 항목은
   * 링크에서 빼고 `aria-current`로 위치를 알린다.
   */
  function navLink(section: ClinicSection, href: string, label: string) {
    if (currentSection === section) {
      return (
        <span className="clinic-header-nav-current" aria-current="page">
          {label}
        </span>
      )
    }
    return <Link href={href}>{label}</Link>
  }

  function navItem(item: SiteNavItem) {
    switch (item) {
      case 'treatments':
        return navLink('treatments', `${hospitalRootUrl}/treatments`, '진료 영역')
      case 'visit':
        return navLink('visit', `${hospitalRootUrl}/visit`, '진료시간·오시는 길')
      case 'doctor':
        return navLink('doctor', `${hospitalRootUrl}/doctor`, '의료진')
      case 'contents':
        return navLink('contents', `${hospitalRootUrl}/contents`, '의료 정보')
      case 'faq':
        return <Link href={`${hospitalRootUrl}#faq`}>자주 묻는 질문</Link>
      case 'website':
        return websiteUrl ? (
          <a className="clinic-header-external-link" href={websiteUrl} target="_blank" rel="noopener noreferrer" style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
            공식 홈페이지
            <ExternalIcon className="clinic-icon clinic-icon--sm" style={{ color: 'currentColor' }} />
          </a>
        ) : null
      default:
        // 'call'은 메뉴 줄이 아니라 오른쪽 전화 상담 버튼이다.
        return null
    }
  }

  const navItems = (
    <>
      {menu.map((item) => (
        <Fragment key={item}>{navItem(item)}</Fragment>
      ))}
    </>
  )

  function barItem(item: SiteMobileBarItem) {
    switch (item) {
      case 'call':
        return (
          <a href={`tel:${phone}`}>
            <PhoneIcon className="clinic-icon" />
            전화
          </a>
        )
      case 'hours':
        // /visit에서는 이 두 항목이 같은 페이지를 다시 열 뿐이었다. 진료시간은 이
        // 페이지의 표로 내려가고, 길찾기는 라벨이 약속한 대로 지도로 나간다(S-1).
        return currentSection === 'visit' ? (
          <a href={`#${VISIT_HOURS_ANCHOR}`}>
            <ClockIcon className="clinic-icon" />
            진료시간
          </a>
        ) : (
          <Link href={`${hospitalRootUrl}/visit#${VISIT_HOURS_ANCHOR}`}>
            <ClockIcon className="clinic-icon" />
            진료시간
          </Link>
        )
      case 'guide':
        return currentSection === 'treatments' ? (
          <span className="clinic-mobile-actionbar-current" aria-current="page">
            <CalendarIcon className="clinic-icon" />
            진료안내
          </span>
        ) : (
          <Link href={`${hospitalRootUrl}/treatments`}>
            <CalendarIcon className="clinic-icon" />
            진료안내
          </Link>
        )
      case 'map':
        return googleMapsUrl ? (
          <a href={googleMapsUrl} target="_blank" rel="noopener noreferrer">
            <MapPinIcon className="clinic-icon" />
            길찾기
          </a>
        ) : (
          <Link href={`${hospitalRootUrl}/visit#${VISIT_HOURS_ANCHOR}`}>
            <MapPinIcon className="clinic-icon" />
            오시는 길
          </Link>
        )
    }
  }

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
              loading="eager"
            />
          ) : (
            <span className="clinic-header-brand-name">{hospitalName}</span>
          )}
          <span className="clinic-header-brand-meta">{brandMeta}</span>
        </Link>

        <nav className="clinic-header-nav" aria-label="병원 섹션">
          {navItems}
        </nav>

        {menu.includes('call') ? (
          <a className="clinic-header-cta" href={`tel:${phone}`}>
            <PhoneIcon className="clinic-icon clinic-icon--sm" style={{ color: 'currentColor' }} />
            <span>전화 상담</span>
            <strong>{phone}</strong>
          </a>
        ) : null}
      </div>

      {/* 모바일 전용 가로 스크롤 nav. desktop에선 숨김. */}
      <nav className="clinic-header-nav-mobile" aria-label="병원 섹션 (모바일)">
        {navItems}
      </nav>

      {bar.length > 0 ? (
        <nav className="clinic-mobile-actionbar" aria-label="빠른 병원 문의">
          {bar.map((item) => (
            <Fragment key={item}>{barItem(item)}</Fragment>
          ))}
        </nav>
      ) : null}
    </header>
  )
}
