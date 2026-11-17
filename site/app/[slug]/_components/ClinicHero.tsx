import { CLINIC_IMAGE_SIZES, CLINIC_IMAGE_QUALITY } from '@/lib/clinic-image-delivery'
import Image from 'next/image'
import Link from 'next/link'

import { seoulToday } from '@/lib/clinic-layout'
import { displayClinicLabels } from '@/lib/clinic-design'
import type { ClinicAccessMode, ClinicMediaMode } from '@/lib/clinic-design'
import { buildClinicHeroHeadline } from '@/lib/clinic-hero-headline'
import { fullClinicAddress } from '@/lib/clinic-schema'

import { HeroCallLabel, HeroScheduleCell, HeroTodayHours } from './HeroToday'
import { CalendarIcon, ClockIcon, MapPinIcon, PhoneIcon } from './icons'
import { regionSentenceLabel } from '@/lib/clinic-region'

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
}

function compactAddress(address: string): string {
  return address.replace(/\s+/g, ' ').trim()
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
                {part}{' '}
              </span>
            ))}
            <strong>{headline.emphasis}</strong>
          </h1>
          <p className="clinic-hero-editorial-lede">
            {heroDescription?.trim() || `${hospitalName}의 진료 영역, 진료시간과 위치를 한곳에서 확인할 수 있습니다.`}
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

    </section>
  )
}
