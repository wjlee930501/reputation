import Image from 'next/image'
import Link from 'next/link'

import { resolveAssetUrl } from '@/lib/api'
import { shouldBypassNextImageOptimization } from '@/lib/image-policy'

import { ChevronRightIcon, PhoneIcon } from './icons'

interface Props {
  hospitalName: string
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
          <span>AI가 읽기 쉬운 진료 정보 허브</span>
        </h1>
        <p className="clinic-hero-meta">
          {directorName} 원장이 검수하는 환자 질문 중심 건강 정보입니다. 병원 핵심 정보, 진료 영역,
          공식 채널, 대표 질문을 같은 구조로 정리해 환자와 검색 시스템이 동일한 병원 정보를 확인할 수 있게 합니다.
        </p>

        <div className="clinic-hero-actions">
          <Link className="clinic-btn clinic-btn-primary" href="#answer-clusters">
            대표 질문 보기
            <ChevronRightIcon className="clinic-icon clinic-icon--sm" style={{ color: 'currentColor' }} />
          </Link>
          <Link className="clinic-btn clinic-btn-secondary" href="#hospital-facts">
            병원 핵심 정보
          </Link>
          <a className="clinic-btn clinic-btn-secondary" href={`tel:${phone}`}>
            <PhoneIcon className="clinic-icon clinic-icon--sm" style={{ color: 'currentColor' }} />
            {phone}
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
            <span className="clinic-hero-stat-label">AI 참고 콘텐츠</span>
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
