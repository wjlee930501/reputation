import { CLINIC_IMAGE_SIZES } from '@/lib/clinic-image-delivery'
import { formatSeoulDate } from '@/lib/clinic-dates'
import Link from 'next/link'
import { SectionLabel } from './SectionLabel'

import { resolveAssetUrl, TYPE_LABELS, type ContentSummary } from '@/lib/api'
import { categoryTagClass } from '@/lib/content-meta'

import { ContentCover } from './ContentCover'
import { ChevronRightIcon } from './icons'

interface Props {
  /** 영문 소제목(섹션 설정·레이아웃 기본, `plan.label`). 없으면 그리지 않는다 — 지금 마크업 그대로. */
  sectionLabel?: string | null
  contents: ContentSummary[]
  hospitalRootUrl: string
  hospitalName: string
  directorName: string
  secondaryLimit?: number
  /** 섹션 설정이 바꾼 제목·안내 문장(레이아웃 편집기). 없으면 지금 문구. */
  title?: string | null
  note?: string | null
}

const PRIORITY_TYPES = ['FAQ', 'DISEASE', 'TREATMENT', 'COLUMN', 'HEALTH', 'LOCAL', 'NOTICE']

function selectFeatured(
  contents: ContentSummary[],
  secondaryLimit: number,
): { primary: ContentSummary | null; rest: ContentSummary[] } {
  if (contents.length === 0) return { primary: null, rest: [] }
  // 우선순위 유형이 있으면 그 첫 항목을 primary로.
  for (const type of PRIORITY_TYPES) {
    const match = contents.find((c) => c.content_type === type)
    if (match) {
      const rest = contents.filter((c) => c.id !== match.id).slice(0, secondaryLimit)
      return { primary: match, rest }
    }
  }
  return { primary: contents[0], rest: contents.slice(1, secondaryLimit + 1) }
}

export function FeaturedContent({
  contents,
  hospitalRootUrl,
  hospitalName,
  directorName,
  secondaryLimit = 4,
  title,
  note,
  sectionLabel = null,
}: Props) {
  const { primary, rest } = selectFeatured(contents, secondaryLimit)
  if (!primary) return null

  const primaryTypeLabel = TYPE_LABELS[primary.content_type] ?? primary.content_type
  const primaryDate = formatSeoulDate(primary.published_at, primary.scheduled_date)

  return (
    <section className="clinic-featured" aria-label="대표 의료 정보">
      <div className="clinic-featured-inner">
        <header className="clinic-section-head">
          {sectionLabel ? <SectionLabel text={sectionLabel} /> : null}
          <h2 className="clinic-section-title">{title ?? '먼저 정리한 글'}</h2>
          <p className="clinic-section-note">
            {note ?? (
              <>
                환자 질문에 답하기 위해 {hospitalName}에서 먼저 정리한 의료 정보입니다.
              </>
            )}
          </p>
        </header>

        <div className={`clinic-lead${rest.length === 0 ? ' clinic-lead--solo' : ''}`}>
          <Link
            href={`${hospitalRootUrl}/contents/${primary.id}`}
            className="clinic-lead-primary"
            aria-label={`대표 콘텐츠 — ${primary.title}`}
          >
            <ContentCover
              type={primary.content_type}
              src={resolveAssetUrl(primary.image_url)}
              variant="featured"
              sizes={rest.length ? CLINIC_IMAGE_SIZES.featured : CLINIC_IMAGE_SIZES.soloFeatured}
            />
            <span className="clinic-lead-kicker">가장 먼저 읽어보면 좋은 글</span>
            <span className={`clinic-tag ${categoryTagClass(primary.content_type)}`}>{primaryTypeLabel}</span>
            <h3 className="clinic-lead-title">{primary.title}</h3>
            {primary.meta_description && (
              <p className="clinic-lead-summary">{primary.meta_description}</p>
            )}
            <span className="clinic-lead-meta">
              <strong>{directorName} 원장</strong>
              <span className="clinic-content-card-meta-dot" aria-hidden="true" />
              <span>{primaryDate}</span>
              <span className="clinic-content-card-meta-dot" aria-hidden="true" />
              <span>{primary.reading_minutes ?? 1}분 분량</span>
            </span>
          </Link>

          {rest.length > 0 && (
            <ol className="clinic-lead-list">
              {rest.map((content) => {
                const typeLabel = TYPE_LABELS[content.content_type] ?? content.content_type
                const date = formatSeoulDate(content.published_at, content.scheduled_date)
                return (
                  <li key={content.id}>
                    <Link
                      href={`${hospitalRootUrl}/contents/${content.id}`}
                      className="clinic-lead-row"
                      aria-label={`${typeLabel} — ${content.title}`}
                    >
                      <span className={`clinic-tag clinic-tag--sm ${categoryTagClass(content.content_type)}`}>
                        {typeLabel}
                      </span>
                      <span className="clinic-lead-row-title">{content.title}</span>
                      <span className="clinic-lead-row-date">{date}</span>
                    </Link>
                  </li>
                )
              })}
            </ol>
          )}
        </div>

        <Link href={`${hospitalRootUrl}/contents`} className="clinic-featured-more">
          의료 정보 전체 보기
          <ChevronRightIcon className="clinic-icon clinic-icon--sm" style={{ color: 'currentColor' }} />
        </Link>
      </div>
    </section>
  )
}
