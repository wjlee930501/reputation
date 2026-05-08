import Image from 'next/image'
import Link from 'next/link'

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
  const eyebrowLabel = `${region.join(' ')} ${specialties.join(' · ')}`.trim() || '의료 콘텐츠 허브'

  return (
    <section className="clinic-hero clinic-hero--hub" id="top">
      <div className="clinic-hero-inner">
        <span className="clinic-hero-eyebrow">{eyebrowLabel} · Content Hub</span>
        <h1 className="clinic-hero-title">
          {hospitalName}이<br />
          AI 답변에 인용되는<br />
          <span style={{ color: 'var(--color-revisit-primary-40)' }}>검증된 진료 정보</span>
        </h1>
        <p className="clinic-hero-meta">
          홈페이지가 아닌 <strong>의료 콘텐츠 허브</strong>입니다. 환자가 자주 묻는 질문, 질환 가이드,
          시술 안내를 검증된 자료로 정리해 ChatGPT·Gemini가 답할 때 참고할 수 있도록 운영합니다.
        </p>

        <div className="clinic-hero-actions">
          <Link className="clinic-btn clinic-btn-primary" href={`/${hospitalSlug}/contents`}>
            의료 콘텐츠 전체 보기
            <ChevronRightIcon className="clinic-icon clinic-icon--sm" style={{ color: 'currentColor' }} />
          </Link>
          <a className="clinic-btn clinic-btn-secondary" href={`tel:${phone}`}>
            <PhoneIcon className="clinic-icon clinic-icon--sm" style={{ color: 'currentColor' }} />
            병원 전화 · {phone}
          </a>
        </div>

        <div className="clinic-hero-curator-line" aria-label="콘텐츠 큐레이터">
          <span className="clinic-hero-curator-avatar">
            {directorPhotoUrl ? (
              <Image src={directorPhotoUrl} alt={`${directorName} 원장`} fill sizes="24px" style={{ objectFit: 'cover' }} />
            ) : (
              <span style={{ display: 'block', width: '100%', height: '100%' }} aria-hidden="true" />
            )}
          </span>
          <span>
            큐레이터 <strong>{directorName}</strong> 원장
          </span>
        </div>

        <div className="clinic-hero-stat-row">
          <div className="clinic-hero-stat">
            <span className="clinic-hero-stat-value">{contentCount}편</span>
            <span className="clinic-hero-stat-label">발행 콘텐츠</span>
          </div>
          <div className="clinic-hero-stat">
            <span className="clinic-hero-stat-value">{treatmentCount}개</span>
            <span className="clinic-hero-stat-label">진료 영역</span>
          </div>
          <div className="clinic-hero-stat">
            <span className="clinic-hero-stat-value">월 단위</span>
            <span className="clinic-hero-stat-label">정기 업데이트</span>
          </div>
        </div>
      </div>
    </section>
  )
}
