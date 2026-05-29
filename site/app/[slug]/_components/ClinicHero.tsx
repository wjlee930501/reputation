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
  const primarySpecialty = specialties[0] || '진료'
  const heroTreatments = treatments.slice(0, 6)
  // 우측 hero 미디어는 가로형(16:9) 밴드 — 세로 원장 사진은 얼굴이 잘리므로
  // 원내 전경(내부·외관·진료실) 사진을 우선 사용한다. 원장 사진은 아래 요약부의
  // 원형 아바타로 노출하므로 큰 미디어에서는 마지막 폴백으로만 쓴다.
  const facilityPhoto =
    photos.find((photo) => photo.source_type === 'PHOTO_CLINIC_INTERIOR') ||
    photos.find((photo) => photo.source_type === 'PHOTO_CLINIC_EXTERIOR') ||
    photos.find((photo) => photo.source_type === 'PHOTO_TREATMENT_ROOM') ||
    photos.find((photo) => photo.source_type !== 'PHOTO_DOCTOR')
  const facilityPhotoUrl = resolveAssetUrl(facilityPhoto?.url)
  const heroPhotoUrl = facilityPhotoUrl || resolvedDirectorPhoto
  const heroShowsFacility = Boolean(facilityPhotoUrl)
  const heroPhotoLabel = heroShowsFacility
    ? facilityPhoto?.title || `${hospitalName} 진료 공간`
    : `${directorName} 대표원장`

  return (
    <section className="clinic-hero clinic-hero--hub" id="top">
      {/* P1-4: 모바일(≤480px)에서 우측 패널이 display:none되므로 hero 최상단에 시설 사진 밴드 노출 */}
      {heroPhotoUrl && (
        <div className="clinic-hero-mobile-photo" aria-hidden="true">
          <Image
            src={heroPhotoUrl}
            alt=""
            fill
            sizes="100vw"
            style={{ objectFit: 'cover', objectPosition: heroShowsFacility ? 'center center' : 'center top' }}
            priority
            unoptimized={shouldBypassNextImageOptimization(heroPhotoUrl)}
          />
        </div>
      )}
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
                style={{ objectFit: 'cover', objectPosition: heroShowsFacility ? 'center center' : 'center top' }}
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

          {/* P1-3: 오늘진료·위치만 표시. 전화는 좌측 CTA + 모바일 하단바로 충분하므로 제거. */}
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
