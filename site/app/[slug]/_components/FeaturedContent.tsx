import Link from 'next/link'

import { resolveAssetUrl, TYPE_LABELS, type ContentSummary } from '@/lib/api'
import { categoryTagClass } from '@/lib/content-meta'

import { ContentCover } from './ContentCover'
import { ChevronRightIcon } from './icons'

interface Props {
  contents: ContentSummary[]
  hospitalRootUrl: string
  hospitalName: string
  directorName: string
  secondaryLimit?: number
}

const PRIORITY_TYPES = ['FAQ', 'DISEASE', 'TREATMENT', 'COLUMN', 'HEALTH', 'LOCAL', 'NOTICE']

function formatDate(value: string | null | undefined, fallback: string) {
  if (!value) return fallback
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return fallback
  return parsed.toLocaleDateString('ko-KR', { year: 'numeric', month: 'long', day: 'numeric' })
}

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
}: Props) {
  const { primary, rest } = selectFeatured(contents, secondaryLimit)
  if (!primary) return null

  const primaryTypeLabel = TYPE_LABELS[primary.content_type] ?? primary.content_type
  const primaryDate = formatDate(primary.published_at, primary.scheduled_date)

  return (
    <section id="contents" className="hub-section" aria-label="대표 의료 정보">
      <div className="hub-container">
        <header className="hub-section-head">
          <h2 className="hub-section-title">먼저 정리한 글</h2>
          <p className="hub-section-note">
            환자 질문에 답하기 위해 {hospitalName}에서 먼저 정리한 의료 정보입니다.
          </p>
        </header>

        <div className={`hub-featured${rest.length === 0 ? ' hub-featured--solo' : ''}`}>
          <Link
            href={`${hospitalRootUrl}/contents/${primary.id}`}
            className="hub-lead"
            aria-label={`대표 콘텐츠 — ${primary.title}`}
          >
            <ContentCover
              type={primary.content_type}
              src={resolveAssetUrl(primary.image_url)}
              variant="featured"
            />
            <span className="hub-lead-kicker">가장 먼저 읽어보면 좋은 글</span>
            <span className={`clinic-tag ${categoryTagClass(primary.content_type)}`}>{primaryTypeLabel}</span>
            <h3 className="hub-lead-title">{primary.title}</h3>
            {primary.meta_description && (
              <p className="hub-lead-summary">{primary.meta_description}</p>
            )}
            <span className="hub-meta">
              <strong>{directorName} 원장</strong>
              <span className="hub-meta-dot" aria-hidden="true" />
              <span>{primaryDate}</span>
              <span className="hub-meta-dot" aria-hidden="true" />
              <span>{primary.reading_minutes ?? 1}분 분량</span>
            </span>
          </Link>

          {rest.length > 0 && (
            <ol className="hub-row-list">
              {rest.map((content) => {
                const typeLabel = TYPE_LABELS[content.content_type] ?? content.content_type
                const date = formatDate(content.published_at, content.scheduled_date)
                return (
                  <li key={content.id}>
                    <Link
                      href={`${hospitalRootUrl}/contents/${content.id}`}
                      className="hub-row"
                      aria-label={`${typeLabel} — ${content.title}`}
                    >
                      <span className={`clinic-tag clinic-tag--sm ${categoryTagClass(content.content_type)}`}>
                        {typeLabel}
                      </span>
                      <span className="hub-row-title">{content.title}</span>
                      <span className="hub-row-date">{date}</span>
                    </Link>
                  </li>
                )
              })}
            </ol>
          )}
        </div>

        <Link href={`${hospitalRootUrl}/contents`} className="hub-more">
          의료 정보 전체 보기
          <ChevronRightIcon className="hub-icon hub-icon--sm" style={{ color: 'currentColor' }} />
        </Link>
      </div>
    </section>
  )
}
