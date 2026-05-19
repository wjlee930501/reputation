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
          {hospitalName}<br />
          <span style={{ color: 'var(--color-revisit-primary-40)' }}>진료 정보 허브</span>
        </h1>
        <p className="clinic-hero-meta">
          진료 과목, 의료진 소개, 오시는 길, 환자가 자주 묻는 질문을 한곳에 정리했습니다.
          증상과 치료 선택에 대한 기본 정보를 확인하고, 개인별 판단은 진료 상담에서 이어가 주세요.
        </p>

        <div className="clinic-hero-actions">
          <Link className="clinic-btn clinic-btn-primary" href={`/${hospitalSlug}/treatments`}>
            진료 안내 보기
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
            <span className="clinic-hero-stat-label">의료 정보 글</span>
          </div>
          <div className="clinic-hero-stat">
            <span className="clinic-hero-stat-value">{treatmentCount}개</span>
            <span className="clinic-hero-stat-label">진료 영역</span>
          </div>
          <div className="clinic-hero-stat">
            <span className="clinic-hero-stat-value">환자 질문</span>
            <span className="clinic-hero-stat-label">중심 구성</span>
          </div>
        </div>
      </div>
    </section>
  )
}
