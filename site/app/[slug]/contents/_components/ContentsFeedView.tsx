import Link from 'next/link'

import { resolveAssetUrl, TYPE_LABELS, type ContentSummary } from '@/lib/api'
import { countLabel } from '@/lib/clinic-counters'
import { categoryTagClass } from '@/lib/content-meta'

import { ContentCover } from '../../_components/ContentCover'
import { ChevronRightIcon } from '../../_components/icons'

const PRIORITY_TYPES = ['FAQ', 'DISEASE', 'TREATMENT', 'COLUMN', 'HEALTH', 'LOCAL', 'NOTICE']

function contentDate(content: ContentSummary): number {
  const value = content.published_at || content.scheduled_date
  const parsed = value ? new Date(value).getTime() : NaN
  return Number.isNaN(parsed) ? 0 : parsed
}

function formatDate(value: string | null | undefined, fallback: string) {
  if (!value) return fallback
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return fallback
  return parsed.toLocaleDateString('ko-KR', { year: 'numeric', month: 'long', day: 'numeric' })
}

export interface ContentsFeedViewProps {
  contents: ContentSummary[]
  hospitalRootUrl: string
  directorName: string
  // null = 전체(필터 없음). URL의 ?type= 이 유효한 유형이 아니면 호출부가 null로 넘긴다.
  activeType: string | null
}

/** 유형 필터를 적용해 대표 글 + 피드 목록을 그리는 순수 프레젠테이션. 서버(정적 폴백,
 * activeType=null 고정)와 클라이언트(useSearchParams로 실시간 activeType) 양쪽에서 쓴다. */
export function ContentsFeedView({ contents, hospitalRootUrl, directorName, activeType }: ContentsFeedViewProps) {
  const counts = new Map<string, number>()
  for (const content of contents) {
    counts.set(content.content_type, (counts.get(content.content_type) ?? 0) + 1)
  }
  const availableTypes = [
    ...PRIORITY_TYPES.filter((type) => counts.has(type)),
    ...Array.from(counts.keys()).filter((type) => !PRIORITY_TYPES.includes(type)),
  ]
  const resolvedActiveType = activeType && counts.has(activeType) ? activeType : null

  const sorted = [...contents].sort((a, b) => contentDate(b) - contentDate(a))
  const filtered = resolvedActiveType ? sorted.filter((c) => c.content_type === resolvedActiveType) : sorted
  const [featured, ...feedRest] = filtered

  const chipHref = (type: string | null) =>
    type ? `${hospitalRootUrl}/contents?type=${type}` : `${hospitalRootUrl}/contents`

  if (contents.length === 0) {
    return (
      <div className="clinic-empty">
        <span className="clinic-empty-title">아직 발행된 콘텐츠가 없습니다</span>
        <p>진료 안내와 건강 정보 글을 준비하고 있습니다.</p>
      </div>
    )
  }

  return (
    <>
      <nav className="clinic-filter-chips" aria-label="유형별 필터">
        <Link
          href={chipHref(null)}
          className="clinic-filter-chip"
          aria-current={resolvedActiveType === null ? 'page' : undefined}
        >
          전체 <span className="clinic-filter-chip-count">{countLabel(contents.length, '편')}</span>
        </Link>
        {availableTypes.map((type) => (
          <Link
            key={type}
            href={chipHref(type)}
            className="clinic-filter-chip"
            aria-current={resolvedActiveType === type ? 'page' : undefined}
          >
            {TYPE_LABELS[type] ?? type}{' '}
            <span className="clinic-filter-chip-count">{countLabel(counts.get(type) ?? 0, '편')}</span>
          </Link>
        ))}
      </nav>

      {featured && (
        <Link
          href={`${hospitalRootUrl}/contents/${featured.id}`}
          className="clinic-feed-featured"
          aria-label={`대표 글 — ${featured.title}`}
        >
          <ContentCover type={featured.content_type} src={resolveAssetUrl(featured.image_url)} variant="featured" />
          <span className="clinic-feed-featured-kicker">
            {resolvedActiveType ? `${TYPE_LABELS[resolvedActiveType] ?? resolvedActiveType} · 최신 글` : '가장 최근 글'}
          </span>
          <span className={`clinic-tag ${categoryTagClass(featured.content_type)}`}>
            {TYPE_LABELS[featured.content_type] ?? featured.content_type}
          </span>
          <h2 className="clinic-feed-featured-title">{featured.faq_question || featured.title}</h2>
          {featured.meta_description && (
            <p className="clinic-feed-featured-excerpt">{featured.meta_description}</p>
          )}
          <span className="clinic-feed-featured-meta">
            <strong>{directorName} 원장</strong>
            <span className="clinic-content-card-meta-dot" aria-hidden="true" />
            <span>{formatDate(featured.published_at, featured.scheduled_date)}</span>
            <span className="clinic-content-card-meta-dot" aria-hidden="true" />
            <span>{featured.reading_minutes ?? 1}분 분량</span>
          </span>
        </Link>
      )}

      {feedRest.length > 0 && (
        <ol className="clinic-feed-list">
          {feedRest.map((content) => {
            const typeLabel = TYPE_LABELS[content.content_type] ?? content.content_type
            return (
              <li key={content.id}>
                <Link
                  href={`${hospitalRootUrl}/contents/${content.id}`}
                  className="clinic-feed-row"
                  aria-label={`${typeLabel} — ${content.title}`}
                >
                  <span className="clinic-feed-row-main">
                    <span className={`clinic-tag clinic-tag--sm ${categoryTagClass(content.content_type)}`}>
                      {typeLabel}
                    </span>
                    <span className="clinic-feed-row-title">{content.faq_question || content.title}</span>
                    {content.meta_description && (
                      <span className="clinic-feed-row-excerpt">{content.meta_description}</span>
                    )}
                  </span>
                  <span className="clinic-feed-row-aside">
                    <span className="clinic-feed-row-date">
                      {formatDate(content.published_at, content.scheduled_date)}
                    </span>
                    <ChevronRightIcon className="clinic-icon clinic-feed-row-arrow" aria-hidden="true" />
                  </span>
                </Link>
              </li>
            )
          })}
        </ol>
      )}
    </>
  )
}
