import Link from 'next/link'

import { resolveAssetUrl, TYPE_LABELS, type ContentSummary } from '@/lib/api'
import { buildTreatmentSlug } from '@/lib/treatment-slug'

import { ContentCover } from './ContentCover'
import { ChevronRightIcon } from './icons'

interface Treatment {
  name: string
  description?: string
}

interface Props {
  contents: ContentSummary[]
  treatments: Treatment[]
  hospitalRootUrl: string
  fallbackImages: string[]
}

export function HomeEditorialGrid({ contents, treatments, hospitalRootUrl, fallbackImages }: Props) {
  const [primary, ...secondary] = contents.slice(0, 3)

  return (
    <section className="clinic-home-overview" aria-label="진료 안내와 건강 정보">
      <div className="clinic-home-overview-inner">
        <div className="clinic-home-treatments">
          <div className="clinic-home-heading-row">
            <div>
              <span className="clinic-home-eyebrow">진료 안내</span>
              <h2>증상에서 시작하는 진료 안내</h2>
            </div>
            <Link href={`${hospitalRootUrl}/treatments`} aria-label="진료 안내 전체 보기">
              전체 보기 <ChevronRightIcon className="clinic-icon clinic-icon--sm" />
            </Link>
          </div>

          <ol className="clinic-home-treatment-list">
            {treatments.slice(0, 4).map((treatment, index) => (
              <li key={`${treatment.name}-${index}`}>
                <Link href={`${hospitalRootUrl}/treatments/${encodeURIComponent(buildTreatmentSlug(treatment.name))}`}>
                  <span className="clinic-home-treatment-number">{String(index + 1).padStart(2, '0')}</span>
                  <span>
                    <strong>{treatment.name}</strong>
                    {treatment.description && <small>{treatment.description}</small>}
                  </span>
                  <ChevronRightIcon className="clinic-icon" aria-hidden="true" />
                </Link>
              </li>
            ))}
          </ol>
        </div>

        {primary && (
          <div className="clinic-home-content">
            <div className="clinic-home-heading-row">
              <div>
                <span className="clinic-home-eyebrow">이번 주 건강정보</span>
                <h2>일상에서 바로 확인하는 건강 가이드</h2>
              </div>
              <Link href={`${hospitalRootUrl}/contents`} aria-label="건강 정보 전체 보기">
                전체 보기 <ChevronRightIcon className="clinic-icon clinic-icon--sm" />
              </Link>
            </div>

            <div className="clinic-home-content-grid">
              <Link href={`${hospitalRootUrl}/contents/${primary.id}`} className="clinic-home-content-primary">
                <ContentCover
                  type={primary.content_type}
                  src={resolveAssetUrl(primary.image_url) ?? fallbackImages[0]}
                  variant="featured"
                />
                <span className="clinic-home-content-type">{TYPE_LABELS[primary.content_type] ?? '건강정보'}</span>
                <h3>{primary.title}</h3>
                {primary.meta_description && <p>{primary.meta_description}</p>}
                <span className="clinic-home-more">자세히 보기 <ChevronRightIcon className="clinic-icon clinic-icon--sm" /></span>
              </Link>

              <div className="clinic-home-content-secondary">
                {secondary.map((content, index) => (
                  <Link key={content.id} href={`${hospitalRootUrl}/contents/${content.id}`}>
                    <ContentCover
                      type={content.content_type}
                      src={resolveAssetUrl(content.image_url) ?? fallbackImages[index + 1] ?? fallbackImages[0]}
                      variant="card"
                    />
                    <span>
                      <small>{TYPE_LABELS[content.content_type] ?? '건강정보'}</small>
                      <strong>{content.title}</strong>
                      <em>자세히 보기</em>
                    </span>
                  </Link>
                ))}
              </div>
            </div>
          </div>
        )}
      </div>
    </section>
  )
}
