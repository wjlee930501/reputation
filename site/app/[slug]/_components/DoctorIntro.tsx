import Image from 'next/image'

import { resolveAssetUrl } from '@/lib/api'
import { shouldBypassNextImageOptimization } from '@/lib/image-policy'

import { StethoscopeIcon } from './MedicalIcons'

interface Props {
  directorName: string
  directorCareer: string
  directorPhotoUrl: string | null
  specialties: string[]
  region: string[]
  contentCount: number
}

export function DoctorIntro({
  directorName,
  directorCareer,
  directorPhotoUrl,
  specialties,
  region,
  contentCount,
}: Props) {
  const resolvedPhoto = resolveAssetUrl(directorPhotoUrl)
  return (
    <section id="curator" className="clinic-section clinic-section--alt">
      <div className="clinic-section-inner">
        <header className="clinic-section-header">
          <span className="clinic-section-eyebrow">Content Curator</span>
          <h2 className="clinic-section-heading">콘텐츠를 큐레이팅하는 의료진</h2>
          <p className="clinic-section-lede">
            이 콘텐츠 허브의 모든 글은 의료진이 검수한 자료를 근거로 작성되며, 발행 전 의료광고 표현
            및 운영 기준 검토를 거칩니다.
          </p>
        </header>

        <div className="clinic-curator">
          {resolvedPhoto ? (
            <div className="clinic-curator-portrait">
              <Image
                src={resolvedPhoto}
                alt={`${directorName} 원장 사진`}
                fill
                sizes="(max-width: 720px) 180px, 240px"
                style={{ objectFit: 'cover' }}
                unoptimized={shouldBypassNextImageOptimization(resolvedPhoto)}
              />
            </div>
          ) : (
            <div
              className="clinic-curator-portrait clinic-curator-portrait--placeholder"
              aria-hidden="true"
            >
              <StethoscopeIcon style={{ color: 'var(--color-revisit-coolgrey-50)' }} />
              <span>원장 사진 준비중</span>
            </div>
          )}

          <div>
            <span className="clinic-curator-eyebrow">큐레이터 · Lead Reviewer</span>
            <h3 className="clinic-curator-name">
              {directorName}
              <small>원장</small>
            </h3>
            <div className="clinic-curator-tag-row">
              {specialties.map((s) => (
                <span key={s} className="clinic-curator-tag">{s}</span>
              ))}
              {region.map((r) => (
                <span key={`r-${r}`} className="clinic-curator-tag">{r}</span>
              ))}
            </div>

            {directorCareer ? (
              <p className="clinic-curator-career">{directorCareer}</p>
            ) : (
              <p className="clinic-curator-career" style={{ color: 'var(--color-revisit-text-helper)' }}>
                약력 정보를 준비하고 있습니다.
              </p>
            )}

            <span className="clinic-curator-rule" aria-hidden="true" />

            <div className="clinic-curator-meta">
              <div className="clinic-curator-meta-cell">
                <span className="clinic-curator-meta-label">검수 콘텐츠</span>
                <span className="clinic-curator-meta-value">{contentCount}편</span>
              </div>
              <div className="clinic-curator-meta-cell">
                <span className="clinic-curator-meta-label">담당 진료</span>
                <span className="clinic-curator-meta-value">
                  {specialties.length > 0 ? specialties.join(' · ') : '-'}
                </span>
              </div>
              <div className="clinic-curator-meta-cell">
                <span className="clinic-curator-meta-label">진료 지역</span>
                <span className="clinic-curator-meta-value">
                  {region.length > 0 ? region.join(' · ') : '-'}
                </span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  )
}
