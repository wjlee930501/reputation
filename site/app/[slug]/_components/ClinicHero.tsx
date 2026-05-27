import Image from 'next/image'
import Link from 'next/link'

import { resolveAssetUrl, type HospitalPhoto } from '@/lib/api'
import { shouldBypassNextImageOptimization } from '@/lib/image-policy'

import { ClinicAvatar } from './ClinicAvatar'
import { ChevronRightIcon, ClockIcon, ExternalIcon, MapPinIcon, PhoneIcon } from './icons'

interface Treatment {
  name: string
}

interface Props {
  hospitalName: string
  hospitalSlug: string
  region: string[]
  specialties: string[]
  phone: string
  directorName: string
  directorPhotoUrl: string | null
  contentCount: number
  treatmentCount: number
  address: string
  businessHours: Record<string, string> | null | undefined
  treatments: Treatment[]
  photos?: HospitalPhoto[]
}

const DAY_LABELS: Record<string, string> = {
  mon: '월',
  tue: '화',
  wed: '수',
  thu: '목',
  fri: '금',
  sat: '토',
  sun: '일',
}

const DAY_KEYS = ['sun', 'mon', 'tue', 'wed', 'thu', 'fri', 'sat']

function todayHours(hours: Record<string, string> | null | undefined): {
  label: string
  time: string
} | null {
  if (!hours) return null
  const key = DAY_KEYS[new Date().getDay()]
  const time = hours[key]
  if (!time) return null
  return { label: DAY_LABELS[key], time }
}

function monogram(name: string): string {
  const trimmed = (name || '').trim()
  if (!trimmed) return '醫'
  // 한국어 이름은 성(첫 글자)을 모노그램으로 사용.
  return trimmed.slice(0, 1)
}

export function ClinicHero({
  hospitalName,
  hospitalSlug,
  region,
  specialties,
  phone,
  directorName,
  directorPhotoUrl,
  contentCount,
  treatmentCount,
  address,
  businessHours,
  treatments,
  photos = [],
}: Props) {
  const eyebrowLabel = [region.join(' '), specialties.join(' · ')]
    .filter(Boolean)
    .join('  ·  ')
  const resolvedDirectorPhoto = resolveAssetUrl(directorPhotoUrl)
  const today = todayHours(businessHours)
  const chipTreatments = treatments.slice(0, 4)
  const remainingTreatments = Math.max(0, treatmentCount - chipTreatments.length)
  const primarySpecialty = specialties[0] || '진료'
  const compactAddress = address.split(' ').slice(0, 4).join(' ') || address
  const heroTreatments = treatments.slice(0, 6)
  const facilityPhoto =
    photos.find((photo) => photo.source_type === 'PHOTO_TREATMENT_ROOM') ||
    photos.find((photo) => photo.source_type === 'PHOTO_CLINIC_INTERIOR') ||
    photos.find((photo) => photo.source_type === 'PHOTO_CLINIC_EXTERIOR') ||
    photos[0]
  const heroPhotoUrl = resolvedDirectorPhoto || resolveAssetUrl(facilityPhoto?.url)
  const isDoctorHero = Boolean(resolvedDirectorPhoto)
  const heroPhotoLabel = isDoctorHero
    ? `${directorName} 대표원장`
    : facilityPhoto?.title || `${hospitalName} 진료 공간`

  return (
    <section className="clinic-hero clinic-hero--hub" id="top">
      <div className="clinic-hero-inner">
        <div className="clinic-hero-lead">
          {eyebrowLabel && <span className="clinic-hero-eyebrow">{eyebrowLabel}</span>}
          <h1 className="clinic-hero-title">
            {hospitalName}
            <span className="clinic-hero-title-sub">{primarySpecialty} 전문 진료</span>
          </h1>
          <p className="clinic-hero-statement">
            통증의 원인을 확인하고, 비수술 치료부터 재활까지 단계적으로 상담합니다.
          </p>
          <p className="clinic-hero-meta">
            무릎·어깨·허리 통증, 스포츠 손상, 도수재활까지 진찰과 영상검사 소견을
            함께 보고 환자 상태에 맞는 치료 방향을 안내합니다.
          </p>

          {heroTreatments.length > 0 && (
            <div className="clinic-hero-treatment-strip" aria-label="주요 진료 영역">
              {heroTreatments.map((treatment) => (
                <Link key={treatment.name} href={`/${hospitalSlug}/treatments`}>
                  {treatment.name}
                </Link>
              ))}
            </div>
          )}

          <dl className="clinic-hero-facts" aria-label="병원 핵심 정보">
            <div>
              <dt>담당 의료진</dt>
              <dd>{directorName} 원장</dd>
            </div>
            <div>
              <dt>진료 범위</dt>
              <dd>비수술·재활 상담</dd>
            </div>
            {today && (
              <div>
                <dt>오늘 진료</dt>
                <dd>{today.label} {today.time}</dd>
              </div>
            )}
            {compactAddress && (
              <div>
                <dt>위치</dt>
                <dd>{compactAddress}</dd>
              </div>
            )}
          </dl>

          <div className="clinic-hero-actions">
            <a className="clinic-btn clinic-btn-cta" href={`tel:${phone}`}>
              <PhoneIcon className="clinic-icon clinic-icon--sm" style={{ color: 'currentColor' }} />
              전화 상담 · {phone}
            </a>
            <Link className="clinic-btn clinic-btn-primary" href={`/${hospitalSlug}/visit`}>
              <MapPinIcon className="clinic-icon clinic-icon--sm" style={{ color: 'currentColor' }} />
              오시는 길·진료시간
            </Link>
            <Link className="clinic-btn clinic-btn-secondary" href={`/${hospitalSlug}/treatments`}>
              진료 안내 보기
              <ChevronRightIcon className="clinic-icon clinic-icon--sm" style={{ color: 'currentColor' }} />
            </Link>
          </div>

          <div className="clinic-hero-byline">
            <ClinicAvatar
              src={resolvedDirectorPhoto}
              alt=""
              wrapperClassName="clinic-hero-byline-avatar"
              fallback={<span className="clinic-hero-byline-monogram">{monogram(directorName)}</span>}
            />
            <span className="clinic-hero-byline-text">
              <strong>{directorName} 원장</strong> 진료 분야를 기준으로 정리한 공식 의료 정보
            </span>
            <span className="clinic-hero-byline-count" aria-label="발행 글 수">
              의료 정보 {contentCount}편
            </span>
          </div>
        </div>

        <aside className="clinic-hero-snapshot clinic-hero-doctor-panel" aria-label="대표 의료진 및 병원 안내">
          {heroPhotoUrl && (
            <div className="clinic-hero-snapshot-media">
              <Image
                src={heroPhotoUrl}
                alt={heroPhotoLabel}
                fill
                sizes="(max-width: 920px) 100vw, 420px"
                style={{ objectFit: 'cover', objectPosition: isDoctorHero ? 'center top' : 'center center' }}
                priority
                unoptimized={shouldBypassNextImageOptimization(heroPhotoUrl)}
              />
              <span className="clinic-hero-snapshot-media-label">
                {heroPhotoLabel}
              </span>
            </div>
          )}
          <div className="clinic-hero-doctor-summary">
            <ClinicAvatar
              src={resolvedDirectorPhoto}
              alt={`${directorName} 원장`}
              wrapperClassName="clinic-hero-doctor-avatar"
              fallback={<span className="clinic-hero-byline-monogram">{monogram(directorName)}</span>}
            />
            <div>
              <span className="clinic-hero-snapshot-label">대표원장</span>
              <strong>{directorName} 원장</strong>
              <span>{primarySpecialty} 전문의</span>
            </div>
          </div>

          {chipTreatments.length > 0 && (
            <div className="clinic-hero-snapshot-block">
              <span className="clinic-hero-snapshot-key">진료 영역</span>
              <div className="clinic-hero-snapshot-chips">
                {chipTreatments.map((t) => (
                  <span key={t.name} className="clinic-hero-snapshot-chip">{t.name}</span>
                ))}
                {remainingTreatments > 0 && (
                  <span className="clinic-hero-snapshot-chip clinic-hero-snapshot-chip--more">
                    +{remainingTreatments}
                  </span>
                )}
              </div>
            </div>
          )}

          <dl className="clinic-hero-snapshot-list">
            {today && (
              <div className="clinic-hero-snapshot-row">
                <dt>
                  <ClockIcon className="clinic-icon clinic-icon--sm" aria-hidden="true" />
                  오늘 진료
                </dt>
                <dd>
                  <span className="clinic-hero-snapshot-today">{today.label}</span> {today.time}
                </dd>
              </div>
            )}
            {address && (
              <div className="clinic-hero-snapshot-row">
                <dt>
                  <MapPinIcon className="clinic-icon clinic-icon--sm" aria-hidden="true" />
                  위치
                </dt>
                <dd>{address}</dd>
              </div>
            )}
            <div className="clinic-hero-snapshot-row">
              <dt>
                <PhoneIcon className="clinic-icon clinic-icon--sm" aria-hidden="true" />
                전화
              </dt>
              <dd>
                <a href={`tel:${phone}`} className="clinic-hero-snapshot-tel">{phone}</a>
              </dd>
            </div>
          </dl>

          <Link href={`/${hospitalSlug}/visit`} className="clinic-hero-snapshot-foot">
            진료 시간 · 오시는 길 자세히
            <ChevronRightIcon className="clinic-icon clinic-icon--sm" style={{ color: 'currentColor' }} />
          </Link>
          <a
            href={`tel:${phone}`}
            className="clinic-hero-snapshot-call"
          >
            <PhoneIcon className="clinic-icon clinic-icon--sm" style={{ color: 'currentColor' }} />
            지금 전화 상담
          </a>
        </aside>
      </div>
      <nav className="clinic-mobile-actionbar" aria-label="빠른 병원 문의">
        <a href={`tel:${phone}`}>
          <PhoneIcon className="clinic-icon" />
          전화
        </a>
        <Link href={`/${hospitalSlug}/visit`}>
          <ClockIcon className="clinic-icon" />
          진료시간
        </Link>
        <Link href={`/${hospitalSlug}/treatments`}>
          <ExternalIcon className="clinic-icon" />
          진료안내
        </Link>
        <Link href={`/${hospitalSlug}/visit`}>
          <MapPinIcon className="clinic-icon" />
          길찾기
        </Link>
      </nav>
    </section>
  )
}
