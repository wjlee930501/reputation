import Image from 'next/image'
import Link from 'next/link'

import { resolveAssetUrl } from '@/lib/api'
import { shouldBypassNextImageOptimization } from '@/lib/image-policy'

import { ChevronRightIcon, PhoneIcon } from './icons'

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
}: Props) {
  const eyebrowLabel = `${region.join(' ')} ${specialties.join(' · ')}`.trim() || hospitalName
  const resolvedDirectorPhoto = resolveAssetUrl(directorPhotoUrl)

  return (
    <section className="clinic-hero clinic-hero--hub" id="top">
      <div className="clinic-hero-inner">
        <span className="clinic-hero-eyebrow">{eyebrowLabel}</span>
        <h1 className="clinic-hero-title">
          {hospitalName}의<br />
          <span style={{ color: 'var(--color-revisit-primary-40)' }}>진료 이야기와 건강 정보</span>
        </h1>
        <p className="clinic-hero-meta">
          {directorName} 원장이 진료실에서 자주 듣는 질문과 진료 안내를 환자가 이해하기 쉬운 글로
          정리합니다. 증상과 치료 선택에 대한 기본 정보를 확인하고, 자세한 판단은 진료 상담에서
          이어가 주세요.
        </p>

        <div className="clinic-hero-actions">
          <Link className="clinic-btn clinic-btn-primary" href={`/${hospitalSlug}/contents`}>
            블로그 글 전체 보기
            <ChevronRightIcon className="clinic-icon clinic-icon--sm" style={{ color: 'currentColor' }} />
          </Link>
          <a className="clinic-btn clinic-btn-secondary" href={`tel:${phone}`}>
            <PhoneIcon className="clinic-icon clinic-icon--sm" style={{ color: 'currentColor' }} />
            병원 전화 · {phone}
          </a>
        </div>

        <div className="clinic-hero-curator-line" aria-label="대표 의료진">
          <span className="clinic-hero-curator-avatar">
            {resolvedDirectorPhoto ? (
              <Image
                src={resolvedDirectorPhoto}
                alt={`${directorName} 원장`}
                fill
                sizes="24px"
                style={{ objectFit: 'cover' }}
                unoptimized={shouldBypassNextImageOptimization(resolvedDirectorPhoto)}
              />
            ) : (
              <span style={{ display: 'block', width: '100%', height: '100%' }} aria-hidden="true" />
            )}
          </span>
          <span>
            <strong>{directorName}</strong> 원장
          </span>
        </div>

        <div className="clinic-hero-stat-row">
          <div className="clinic-hero-stat">
            <span className="clinic-hero-stat-value">{contentCount}편</span>
            <span className="clinic-hero-stat-label">블로그 글</span>
          </div>
          <div className="clinic-hero-stat">
            <span className="clinic-hero-stat-value">{treatmentCount}개</span>
            <span className="clinic-hero-stat-label">진료 영역</span>
          </div>
          <div className="clinic-hero-stat">
            <span className="clinic-hero-stat-value">원장 검수</span>
            <span className="clinic-hero-stat-label">의료 정보</span>
          </div>
        </div>
      </div>
    </section>
  )
}
