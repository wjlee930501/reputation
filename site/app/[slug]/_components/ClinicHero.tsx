import Image from 'next/image'
import Link from 'next/link'

import { resolveAssetUrl } from '@/lib/api'

import { ClinicAvatar } from './ClinicAvatar'
import { CalendarIcon, ClockIcon, MapPinIcon, PhoneIcon } from './icons'

interface Props {
  hospitalName: string
  hospitalSlug: string
  hospitalRootUrl: string
  region: string[]
  specialties: string[]
  phone: string
  directorName: string
  directorPhotoUrl: string | null
  heroPhotoUrl?: string | null
  address: string
  businessHours: Record<string, string> | null | undefined
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
  hospitalSlug,
  hospitalRootUrl,
  region,
  specialties,
  phone,
  directorName,
  directorPhotoUrl,
  heroPhotoUrl = null,
  address,
  businessHours,
}: Props) {
  const resolvedDirectorPhoto = resolveAssetUrl(directorPhotoUrl)
  const photo = heroPhotoUrl || resolvedDirectorPhoto
  const today = todayHours(businessHours)
  const saturday = businessHours?.sat
  const specialtyLabel = specialties.filter(Boolean).join(' · ')
  const locationLabel = region.filter(Boolean).join(' ')
  const isJangClinic = hospitalSlug === 'jangpyeonhanoegwayiweon'

  const headline = isJangClinic
    ? { first: '대장항문 질환,', second: '정확히 설명하고', accent: '필요한 치료만 권합니다' }
    : {
        first: specialtyLabel || hospitalName,
        second: '증상을 정확히 확인하고',
        accent: '필요한 치료만 안내합니다',
      }

  return (
    <section className="clinic-hero clinic-hero--editorial" id="top">
      <div className="clinic-hero-editorial-grid">
        <div className="clinic-hero-editorial-copy">
          <span className="clinic-hero-editorial-kicker">
            {[locationLabel, specialtyLabel].filter(Boolean).join(' · ')}
          </span>
          <h1 className="clinic-hero-editorial-title">
            <span>{headline.first}</span>
            <span>{headline.second}</span>
            <strong>{headline.accent}</strong>
          </h1>
          <p className="clinic-hero-editorial-lede">
            환자 한 분 한 분의 상황을 충분히 듣고, 확인된 결과를 바탕으로 치료 방향을 함께 결정합니다.
          </p>
          <div className="clinic-hero-editorial-actions">
            <a className="clinic-btn clinic-btn-cta" href={`tel:${phone}`}>
              <PhoneIcon className="clinic-icon clinic-icon--sm" />
              전화 상담
            </a>
            <Link className="clinic-btn clinic-btn-secondary" href={`${hospitalRootUrl}/visit`}>
              <MapPinIcon className="clinic-icon clinic-icon--sm" />
              오시는 길
            </Link>
          </div>
        </div>

        <div className={`clinic-hero-editorial-photo${photo ? '' : ' is-empty'}`}>
          {photo ? (
            heroPhotoUrl ? (
              <Image
                src={heroPhotoUrl}
                alt={`${directorName} 원장 진료 상담 모습`}
                fill
                priority
                quality={84}
                sizes="(max-width: 920px) 100vw, 58vw"
                className="clinic-hero-editorial-image"
              />
            ) : (
              <ClinicAvatar
                src={resolvedDirectorPhoto}
                alt={`${directorName} 원장`}
                wrapperClassName="clinic-hero-editorial-avatar"
                fallback={<span className="clinic-hero-editorial-monogram">{directorName.slice(0, 1)}</span>}
              />
            )
          ) : (
            <div className="clinic-hero-editorial-fallback">
              <span>{hospitalName}</span>
              <strong>{directorName} 원장</strong>
              <small>{specialtyLabel || '진료 안내'}</small>
            </div>
          )}
        </div>
      </div>

      <dl className="clinic-hero-fact-rail" aria-label="병원 빠른 안내">
        <div>
          <ClockIcon className="clinic-icon" />
          <dt>오늘 진료</dt>
          <dd>{today ? (today.closed ? '오늘 휴진' : today.time) : '방문 전 전화 확인'}</dd>
        </div>
        <div>
          <PhoneIcon className="clinic-icon" />
          <dt>전화</dt>
          <dd><a href={`tel:${phone}`}>{phone}</a></dd>
        </div>
        <div className="clinic-hero-fact-rail-address">
          <MapPinIcon className="clinic-icon" />
          <dt>위치</dt>
          <dd>{compactAddress(address)}</dd>
        </div>
        <div>
          <CalendarIcon className="clinic-icon" />
          <dt>토요일 진료</dt>
          <dd>{saturday || '방문 전 전화 확인'}</dd>
        </div>
      </dl>

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
    </section>
  )
}
