import Image from 'next/image'
import Link from 'next/link'

import { nextOpenDay, uniformWeeklyHours } from '@/lib/business-hours'
import { displayClinicLabels } from '@/lib/clinic-design'
import type { ClinicAccessMode, ClinicMediaMode } from '@/lib/clinic-design'
import { buildClinicHeroHeadline } from '@/lib/clinic-hero-headline'
import { fullClinicAddress } from '@/lib/clinic-schema'

import { CalendarIcon, ClockIcon, MapPinIcon, PhoneIcon } from './icons'

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

const DAY_FULL_LABELS: Record<string, string> = {
  mon: '월요일',
  tue: '화요일',
  wed: '수요일',
  thu: '목요일',
  fri: '금요일',
  sat: '토요일',
  sun: '일요일',
}

function seoulDayKey(): string {
  return new Intl.DateTimeFormat('en-US', { weekday: 'short', timeZone: 'Asia/Seoul' })
    .format(new Date())
    .toLowerCase()
}

function todayHours(hours: Record<string, string> | null | undefined): {
  label: string
  time: string
  closed: boolean
} | null {
  if (!hours) return null
  const key = seoulDayKey()
  const time = hours[key]
  if (!time) return null
  const closed = /휴진|휴무|closed/i.test(time)
  return { label: DAY_FULL_LABELS[key] ?? key, time, closed }
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
  const today = todayHours(businessHours)
  const saturday = businessHours?.sat
  // 요일 편차가 없는 병원에서 `토요일 진료`는 `오늘 진료`와 같은 값을 반복할 뿐이다.
  // 그 칸을 환자가 실제로 궁금해하는 사실(연중무휴)로 바꾼다.
  const uniformHours = uniformWeeklyHours(businessHours)
  // 오늘이 휴진이면 지나간 요일의 시간을 보여 줄 게 아니라 다음에 언제 여는지를
  // 알려야 한다(S-8).
  const upcoming = today?.closed ? nextOpenDay(businessHours, seoulDayKey()) : null
  const specialtyLabel = displayClinicLabels(specialties).join(' · ')
  const locationLabel = displayClinicLabels(region).join(' ')
  const headline = buildClinicHeroHeadline({
    approvedHeadline: heroHeadline,
    accessMode,
    specialtyLabel,
    hospitalName,
  })

  return (
    <section
      className={`hub-hero hub-hero--access-${accessMode} hub-hero--media-${mediaMode}`}
      id="top"
    >
      <div className="hub-container">
        <div className="hub-hero-grid">
          <div className="hub-hero-copy">
            <p className="hub-hero-kicker">
              {[locationLabel, specialtyLabel].filter(Boolean).join(' · ')}
            </p>
            {/* 조각 사이의 `{' '}`는 장식이 아니다 — 이게 없으면 제목 텍스트가
                `대장항문외과,의료진과 진료 정보를방문 전에 확인하세요`로 읽힌다. */}
            <h1
              className={`hub-hero-title${headline.explicitLines ? ' hub-hero-title--lines' : ''}`}
            >
              {headline.lead.map((part, index) => (
                <span key={`${part}-${index}`}>
                  {part}{' '}
                </span>
              ))}
              <strong>{headline.emphasis}</strong>
            </h1>
            <p className="hub-hero-lede">
              {heroDescription?.trim() || `${hospitalName}의 진료 영역, 진료시간과 위치를 한곳에서 확인할 수 있습니다.`}
            </p>
            <div className="hub-hero-actions">
              {accessMode === 'specialist' ? (
                <>
                  <Link className="hub-btn hub-btn--primary" href={`${hospitalRootUrl}/doctor`}>
                    의료진 보기
                  </Link>
                  <Link className="hub-btn hub-btn--secondary" href={`${hospitalRootUrl}/treatments`}>
                    진료 영역
                  </Link>
                </>
              ) : (
                <>
                  <a className="hub-btn hub-btn--primary" href={`tel:${phone}`}>
                    <PhoneIcon className="hub-icon hub-icon--sm" style={{ color: 'currentColor' }} />
                    {/* 오늘 휴진인데 '전화 상담'만 있으면 환자는 받지 않는 번호로 건다.
                        사실을 먼저 말하고, 전화는 그대로 걸 수 있게 둔다(S-8). */}
                    {today?.closed ? '오늘 휴진 · 전화 문의' : '전화 상담'}
                  </a>
                  <Link className="hub-btn hub-btn--secondary" href={`${hospitalRootUrl}/visit`}>
                    <MapPinIcon className="hub-icon hub-icon--sm" style={{ color: 'currentColor' }} />
                    오시는 길
                  </Link>
                </>
              )}
            </div>
          </div>

          {heroPhotoUrl ? (
            <div className="hub-hero-media">
              <Image
                src={heroPhotoUrl}
                alt={mediaMode === 'brand-graphic' ? `${hospitalName} 브랜드 그래픽` : `${hospitalName} 진료 공간`}
                fill
                priority
                loading="eager"
                quality={84}
                sizes="(max-width: 1023px) 100vw, 58vw"
              />
            </div>
          ) : (
            <div className="hub-hero-media hub-hero-media--empty">
              <div>
                <span>진료 안내</span>
                <strong>{hospitalName}</strong>
                <span>{directorName ? `${directorName} 원장` : specialtyLabel || '진료 안내'}</span>
              </div>
            </div>
          )}
        </div>

        <dl className="hub-hero-facts" aria-label="병원 빠른 안내">
          <div className="hub-fact">
            <ClockIcon className="hub-icon" />
            <dt>오늘 진료</dt>
            <dd>{today ? (today.closed ? '오늘 휴진' : today.time) : '방문 전 전화 확인'}</dd>
          </div>
          <div className="hub-fact">
            <PhoneIcon className="hub-icon" />
            <dt>전화</dt>
            <dd><a href={`tel:${phone}`}>{phone}</a></dd>
          </div>
          <div className="hub-fact">
            <MapPinIcon className="hub-icon" />
            <dt>위치</dt>
            <dd>{compactAddress(fullClinicAddress(address, addressDetail))}</dd>
          </div>
          <div className="hub-fact">
            <CalendarIcon className="hub-icon" />
            <dt>{uniformHours ? '휴무일' : upcoming ? '다음 진료' : '토요일 진료'}</dt>
            <dd>
              {uniformHours
                ? '연중무휴'
                : upcoming
                  ? `${upcoming.label} ${upcoming.time}`
                  : saturday || '방문 전 전화 확인'}
            </dd>
          </div>
        </dl>
      </div>
    </section>
  )
}
