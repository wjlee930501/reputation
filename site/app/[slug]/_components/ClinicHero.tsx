import { CLINIC_IMAGE_SIZES, CLINIC_IMAGE_QUALITY } from '@/lib/clinic-image-delivery'
import Image from 'next/image'
import Link from 'next/link'
import type { CSSProperties, ReactNode } from 'react'
import { KeepTogether } from './KeepTogether'

import type { SiteHeroBlock } from '@/lib/clinic-site-config'

import { seoulToday } from '@/lib/clinic-layout'
import { displayClinicLabels } from '@/lib/clinic-design'
import type { ClinicAccessMode, ClinicMediaMode } from '@/lib/clinic-design'
import { buildClinicHeroHeadline } from '@/lib/clinic-hero-headline'
import { fullClinicAddress } from '@/lib/clinic-schema'

import { HeroCallLabel, HeroScheduleCell, HeroTodayHours } from './HeroToday'
import { CalendarIcon, CheckIcon, ClockIcon, MapPinIcon, PhoneIcon } from './icons'
import { regionSentenceLabel } from '@/lib/clinic-region'
import { isPreviewAssetUrl } from '@/lib/site-preview-assets'

interface Props {
  hospitalName: string
  hospitalRootUrl: string
  region: string[]
  specialties: string[]
  phone: string
  directorName: string
  heroPhotoUrl?: string | null
  address: string
  addressDetail?: string | null
  businessHours: Record<string, string> | null | undefined
  accessMode: ClinicAccessMode
  mediaMode: ClinicMediaMode
  heroHeadline?: string | null
  heroDescription?: string | null
  /**
   * B안 섹션 설정이 고른 정보 띠 칸(`resolveHeroBlocks`). null이면 지금 띠(오늘 진료·전화·위치·진료시간)를
   * 지금 마크업 그대로 그린다 — 레이아웃 도입 전 병원과 설정이 없는 병원은 언제나 null이다.
   */
  blocks?: SiteHeroBlock[] | null
  /** '예약' 칸이 여는 네이버 플레이스 주소(`naverBookingUrl`). */
  bookingUrl?: string | null
  /** 섹션 설정의 첫 화면 모양 표지(`heroLookAttributes`) — 비어 있으면 지금 마크업 그대로. */
  lookAttributes?: Record<string, unknown>
}

function compactAddress(address: string): string {
  return address.replace(/\s+/g, ' ').trim()
}

/** 칸 너비 비율 — 지금 띠(`1.2fr 1fr 1.55fr 1.1fr`)와 같은 값을 칸마다 둔다. */
const BLOCK_WIDTH: Record<SiteHeroBlock, string> = {
  today: '1.2fr',
  phone: '1fr',
  location: '1.55fr',
  hours: '1.1fr',
  booking: '1fr',
}

export function ClinicHero({
  hospitalName,
  hospitalRootUrl,
  region,
  specialties,
  phone,
  directorName,
  heroPhotoUrl = null,
  address,
  addressDetail = null,
  businessHours,
  accessMode,
  mediaMode,
  heroHeadline = null,
  heroDescription = null,
  blocks = null,
  bookingUrl = null,
  lookAttributes = {},
}: Props) {
  // 오늘에 따라 바뀌는 칸(오늘 진료·전화 버튼·다음 진료)은 HeroToday가 브라우저에서 한국 시간으로
  // 다시 잰다 — 캐시된 HTML이 지난 요일 값을 보이지 않게. 연중무휴·다음 진료·주말 규칙은 그 파일에 있다.
  const renderedTodayKey = seoulToday().key
  const specialtyLabel = displayClinicLabels(specialties).join(' · ')
  const locationLabel = regionSentenceLabel(region)
  const headline = buildClinicHeroHeadline({
    approvedHeadline: heroHeadline,
    accessMode,
    specialtyLabel,
    hospitalName,
  })

  return (
    <section
      className={`clinic-hero clinic-hero--editorial clinic-hero--access-${accessMode} clinic-hero--media-${mediaMode}`}
      id="top"
      {...lookAttributes}
    >
      <div className="clinic-hero-editorial-grid">
        <div className="clinic-hero-editorial-copy">
          <span className="clinic-hero-editorial-kicker">
            {[locationLabel, specialtyLabel].filter(Boolean).join(' · ')}
          </span>
          {/* 조각 사이의 `{' '}`는 장식이 아니다 — 이게 없으면 제목 텍스트가
              `대장항문외과,의료진과 진료 정보를방문 전에 확인하세요`로 읽힌다. */}
          <h1
            className={`clinic-hero-editorial-title${
              headline.explicitLines ? ' clinic-hero-editorial-title--lines' : ''
            }`}
          >
            {headline.lead.map((part, index) => (
              <span key={`${part}-${index}`}>
                <KeepTogether text={part} compounds />{' '}
              </span>
            ))}
            <strong><KeepTogether text={headline.emphasis} compounds /></strong>
          </h1>
          <p className="clinic-hero-editorial-lede">
            <KeepTogether
              text={heroDescription?.trim() || `${hospitalName}의 진료 영역, 진료시간과 위치를 한곳에서 확인할 수 있습니다.`}
            />
          </p>
          <div className="clinic-hero-editorial-actions">
            {accessMode === 'specialist' ? (
              <>
                <Link className="clinic-btn clinic-btn-cta" href={`${hospitalRootUrl}/doctor`}>
                  의료진 보기
                </Link>
                <Link className="clinic-btn clinic-btn-secondary" href={`${hospitalRootUrl}/treatments`}>
                  진료 영역
                </Link>
              </>
            ) : (
              <>
                <a className="clinic-btn clinic-btn-cta" href={`tel:${phone}`}>
                  <PhoneIcon className="clinic-icon clinic-icon--sm" />
                  {/* 오늘 휴진인데 '전화 상담'만 있으면 환자는 받지 않는 번호로 건다.
                      사실을 먼저 말하고, 전화는 그대로 걸 수 있게 둔다(S-8). */}
                  <HeroCallLabel businessHours={businessHours} renderedTodayKey={renderedTodayKey} />
                </a>
                <Link className="clinic-btn clinic-btn-secondary" href={`${hospitalRootUrl}/visit`}>
                  <MapPinIcon className="clinic-icon clinic-icon--sm" />
                  오시는 길
                </Link>
              </>
            )}
          </div>
        </div>

        <div className={`clinic-hero-editorial-photo${heroPhotoUrl ? '' : ' is-empty'}`}>
          {heroPhotoUrl ? (
            <Image
              src={heroPhotoUrl}
              unoptimized={isPreviewAssetUrl(heroPhotoUrl)}
              alt={mediaMode === 'brand-graphic' ? `${hospitalName} 브랜드 그래픽` : `${hospitalName} 진료 공간`}
              fill
              preload
              quality={CLINIC_IMAGE_QUALITY}
              sizes={CLINIC_IMAGE_SIZES.hero}
              className="clinic-hero-editorial-image"
            />
          ) : (
            <div className="clinic-hero-editorial-fallback">
              <span>진료 정보 허브</span>
              <strong>{hospitalName}</strong>
              <small>{directorName ? `${directorName} 원장` : specialtyLabel || '진료 안내'}</small>
            </div>
          )}
        </div>
      </div>

      {blocks ? (
        blocks.length === 0 ? null : <CustomFactRail
          blocks={blocks}
          phone={phone}
          address={address}
          addressDetail={addressDetail}
          businessHours={businessHours}
          renderedTodayKey={renderedTodayKey}
          bookingUrl={bookingUrl}
        />
      ) : (
      <dl className="clinic-hero-fact-rail" aria-label="병원 빠른 안내">
        <div>
          <ClockIcon className="clinic-icon" />
          <dt>오늘 진료</dt>
          <dd><HeroTodayHours businessHours={businessHours} renderedTodayKey={renderedTodayKey} /></dd>
        </div>
        <div>
          <PhoneIcon className="clinic-icon" />
          <dt>전화</dt>
          <dd><a href={`tel:${phone}`}>{phone}</a></dd>
        </div>
        <div className="clinic-hero-fact-rail-address">
          <MapPinIcon className="clinic-icon" />
          <dt>위치</dt>
          <dd>{compactAddress(fullClinicAddress(address, addressDetail))}</dd>
        </div>
        <div>
          <CalendarIcon className="clinic-icon" />
          <HeroScheduleCell businessHours={businessHours} renderedTodayKey={renderedTodayKey} />
        </div>
      </dl>
      )}

    </section>
  )
}

/**
 * 섹션 설정이 고른 칸으로 그리는 정보 띠(B안). 칸의 내용·마크업은 지금 띠의 같은 칸과 같고, 순서·개수와
 * '예약' 칸만 다르다. 당일·야간 진료형의 맨 앞 칸 강조색은 쓰지 않는다(globals.css — B 띠 배경과 같은 색).
 */
function CustomFactRail({
  blocks,
  phone,
  address,
  addressDetail,
  businessHours,
  renderedTodayKey,
  bookingUrl,
}: {
  blocks: SiteHeroBlock[]
  phone: string
  address: string
  addressDetail: string | null
  businessHours: Record<string, string> | null | undefined
  renderedTodayKey: ReturnType<typeof seoulToday>['key']
  bookingUrl: string | null
}) {
  const cells: Record<SiteHeroBlock, ReactNode> = {
    today: (
      <div key="today">
        <ClockIcon className="clinic-icon" />
        <dt>오늘 진료</dt>
        <dd><HeroTodayHours businessHours={businessHours} renderedTodayKey={renderedTodayKey} /></dd>
      </div>
    ),
    phone: (
      <div key="phone">
        <PhoneIcon className="clinic-icon" />
        <dt>전화</dt>
        <dd><a href={`tel:${phone}`}>{phone}</a></dd>
      </div>
    ),
    location: (
      <div key="location" className="clinic-hero-fact-rail-address">
        <MapPinIcon className="clinic-icon" />
        <dt>위치</dt>
        <dd>{compactAddress(fullClinicAddress(address, addressDetail))}</dd>
      </div>
    ),
    hours: (
      <div key="hours">
        <CalendarIcon className="clinic-icon" />
        <HeroScheduleCell businessHours={businessHours} renderedTodayKey={renderedTodayKey} />
      </div>
    ),
    booking: bookingUrl ? (
      <div key="booking" className="clinic-hero-fact-rail-booking">
        <CheckIcon className="clinic-icon" />
        <dt>예약</dt>
        <dd>
          <a href={bookingUrl} target="_blank" rel="noopener noreferrer">
            네이버에서 예약·문의
            <span className="sr-only"> (새 창)</span>
          </a>
        </dd>
      </div>
    ) : null,
  }
  const shown = blocks.filter((block) => cells[block] !== null)
  return (
    <dl
      className={`clinic-hero-fact-rail clinic-hero-fact-rail--custom${shown.length % 2 ? ' clinic-hero-fact-rail--odd' : ''}`}
      style={{ '--clinic-rail-columns': shown.map((block) => BLOCK_WIDTH[block]).join(' ') } as CSSProperties}
      aria-label="병원 빠른 안내"
    >
      {shown.map((block) => cells[block])}
    </dl>
  )
}
