import Link from 'next/link'

import { resolveAssetUrl } from '@/lib/api'

import { ClinicAvatar } from './ClinicAvatar'
import { HeroLineArt } from './brand'
import { ChevronRightIcon, ClockIcon, MapPinIcon, PhoneIcon, StethoscopeIcon } from './icons'

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
  // AE가 검수해 입력한 구조화 자격 정보(예: "정형외과 전문의"). 자유 입력 specialty로
  // "전문의"를 만들어내면 법적 보호 명칭 오용이 되므로 이 필드가 있을 때만 노출한다.
  boardCertifications?: string[] | null
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

// 서버(UTC)가 아닌 한국 시간 기준의 요일 키. ISR 렌더 시점에 UTC 요일을 쓰면
// KST 00:00~09:00 사이에 전날 요일이 표시된다.
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
  boardCertifications = null,
}: Props) {
  const kicker = [region.join(' '), specialties.join(' · ')].filter(Boolean).join('  ·  ')
  const resolvedDirectorPhoto = resolveAssetUrl(directorPhotoUrl)
  const today = todayHours(businessHours)
  // 모든 진료 문구는 프로파일 데이터(specialties·treatments)에서만 파생한다.
  const specialtyLabel = specialties.filter(Boolean).join('·')
  // specialty 자유 입력에 이미 "협진"이 포함될 수 있어(예: "재활의학 협진") 중복 표기를 방지한다.
  const heroTitleSub = specialtyLabel
    ? `${specialtyLabel}${specialtyLabel.includes('협진') ? '' : ' 협진'} 진료`
    : '진료 안내'
  // 검수된 전문의 자격이 있을 때만 "전문의" 표기. 없으면 "대표원장"만 사용.
  const doctorRoleLabel = boardCertifications?.find(Boolean) ?? '대표원장'
  const treatmentNames = treatments.map((t) => t.name).filter(Boolean)
  // 진료 항목을 얇은 라인 칩으로 노출 — 히어로에 스캔 가능한 텍스처 앵커를 만든다.
  const chipNames = treatmentNames.slice(0, 5)

  return (
    <section className="clinic-hero clinic-hero--hub" id="top">
      <HeroLineArt className="clinic-hero-artwork" aria-hidden="true" />
      <div className="clinic-hero-inner">
        <div className="clinic-hero-lead">
          {kicker && <span className="clinic-hero-kicker">{kicker}</span>}
          <h1 className="clinic-hero-title">
            {hospitalName}
            <span className="clinic-hero-title-sub">{heroTitleSub}</span>
          </h1>
          <p className="clinic-hero-statement">
            증상의 원인을 먼저 확인하고, 환자 상태에 맞는 치료 방향을 상담합니다.
          </p>

          {chipNames.length > 0 && (
            <ul className="clinic-hero-scope" aria-label="주요 진료 항목">
              {chipNames.map((name) => (
                <li key={name}>{name}</li>
              ))}
              {treatmentNames.length > chipNames.length && (
                <li className="clinic-hero-scope-more">
                  <Link href={`/${hospitalSlug}/treatments`}>+{treatmentNames.length - chipNames.length}</Link>
                </li>
              )}
            </ul>
          )}

          <div className="clinic-hero-actions">
            <a className="clinic-btn clinic-btn-cta" href={`tel:${phone}`}>
              <PhoneIcon className="clinic-icon clinic-icon--sm" style={{ color: 'currentColor' }} />
              전화 상담 · {phone}
            </a>
            <Link className="clinic-btn clinic-btn-secondary" href={`/${hospitalSlug}/visit`}>
              <MapPinIcon className="clinic-icon clinic-icon--sm" style={{ color: 'currentColor' }} />
              오시는 길·진료시간
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

        <aside className="clinic-hero-card" aria-label="대표 의료진 및 병원 안내">
          <div className="clinic-hero-card-doctor">
            <ClinicAvatar
              src={resolvedDirectorPhoto}
              alt={`${directorName} 원장`}
              wrapperClassName="clinic-hero-card-avatar"
              fallback={<span className="clinic-hero-byline-monogram">{monogram(directorName)}</span>}
            />
            <div className="clinic-hero-card-doctor-meta">
              <span className="clinic-hero-card-eyebrow">대표원장</span>
              <strong>{directorName} 원장</strong>
              <span className="clinic-hero-card-role">{doctorRoleLabel}</span>
            </div>
          </div>

          <dl className="clinic-hero-card-facts">
            {specialtyLabel && (
              <div className="clinic-hero-card-fact">
                <dt>
                  <StethoscopeIcon className="clinic-icon clinic-icon--sm" aria-hidden="true" />
                  진료과목
                </dt>
                <dd>{specialties.filter(Boolean).join(' · ')}</dd>
              </div>
            )}
            {today && (
              <div className="clinic-hero-card-fact">
                <dt>
                  <ClockIcon className="clinic-icon clinic-icon--sm" aria-hidden="true" />
                  오늘 진료
                </dt>
                <dd>
                  <span
                    className={`clinic-status-dot${today.closed ? ' is-closed' : ''}`}
                    aria-hidden="true"
                  />
                  {today.closed ? '오늘 휴진' : today.time}
                </dd>
              </div>
            )}
            <div className="clinic-hero-card-fact">
              <dt>
                <PhoneIcon className="clinic-icon clinic-icon--sm" aria-hidden="true" />
                전화
              </dt>
              <dd>
                <a href={`tel:${phone}`} className="clinic-hero-card-tel">
                  {phone}
                </a>
              </dd>
            </div>
            {address && (
              <div className="clinic-hero-card-fact">
                <dt>
                  <MapPinIcon className="clinic-icon clinic-icon--sm" aria-hidden="true" />
                  위치
                </dt>
                <dd>{address}</dd>
              </div>
            )}
          </dl>

          <a href={`tel:${phone}`} className="clinic-hero-card-call">
            <PhoneIcon className="clinic-icon clinic-icon--sm" style={{ color: 'currentColor' }} />
            지금 전화 상담
          </a>
          <Link href={`/${hospitalSlug}/visit`} className="clinic-hero-card-foot">
            진료 시간 · 오시는 길 자세히
            <ChevronRightIcon className="clinic-icon clinic-icon--sm" style={{ color: 'currentColor' }} />
          </Link>
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
          <ChevronRightIcon className="clinic-icon" />
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
