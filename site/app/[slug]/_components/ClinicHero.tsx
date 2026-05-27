import Link from 'next/link'

import { resolveAssetUrl } from '@/lib/api'

import { ClinicAvatar } from './ClinicAvatar'
import { ChevronRightIcon, ClockIcon, MapPinIcon, PhoneIcon } from './icons'

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
}: Props) {
  const eyebrowLabel = [region.join(' '), specialties.join(' · ')]
    .filter(Boolean)
    .join('  ·  ')
  const resolvedDirectorPhoto = resolveAssetUrl(directorPhotoUrl)
  const today = todayHours(businessHours)
  const chipTreatments = treatments.slice(0, 4)
  const remainingTreatments = Math.max(0, treatmentCount - chipTreatments.length)

  return (
    <section className="clinic-hero clinic-hero--hub" id="top">
      <div className="clinic-hero-inner">
        <div className="clinic-hero-lead">
          {eyebrowLabel && <span className="clinic-hero-eyebrow">{eyebrowLabel}</span>}
          <h1 className="clinic-hero-title">
            {hospitalName}
            <span className="clinic-hero-title-sub">진료 정보 허브</span>
          </h1>
          <p className="clinic-hero-meta">
            진료 과목, 의료진 소개, 오시는 길, 환자가 자주 묻는 질문을 한곳에 정리했습니다.
            증상과 치료 선택에 대한 기본 정보를 확인하고, 개인별 판단은 진료 상담에서 이어가 주세요.
          </p>

          <div className="clinic-hero-actions">
            <a className="clinic-btn clinic-btn-cta" href={`tel:${phone}`}>
              <PhoneIcon className="clinic-icon clinic-icon--sm" style={{ color: 'currentColor' }} />
              전화 상담 · {phone}
            </a>
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
              <strong>{directorName} 원장</strong> 진료 분야 기준으로 정리한 의료 정보
            </span>
            <span className="clinic-hero-byline-count" aria-label="발행 글 수">
              총 {contentCount}편
            </span>
          </div>
        </div>

        <aside className="clinic-hero-snapshot" aria-label="병원 안내 요약">
          <span className="clinic-hero-snapshot-label">병원 안내</span>

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
        </aside>
      </div>
    </section>
  )
}
