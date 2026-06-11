import Image from 'next/image'
import Link from 'next/link'

import { resolveAssetUrl, TYPE_LABELS, type ContentSummary } from '@/lib/api'
import { categoryTagClass } from '@/lib/content-meta'
import { shouldBypassNextImageOptimization } from '@/lib/image-policy'

import { ChevronRightIcon } from './icons'

interface Props {
  contents: ContentSummary[]
  hospitalSlug: string
  hospitalName: string
  directorName: string
}

const PRIORITY_TYPES = ['FAQ', 'DISEASE', 'TREATMENT', 'COLUMN', 'HEALTH', 'LOCAL', 'NOTICE']

function formatDate(value: string | null | undefined, fallback: string) {
  if (!value) return fallback
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return fallback
  return parsed.toLocaleDateString('ko-KR', { year: 'numeric', month: 'long', day: 'numeric' })
}

function selectFeatured(contents: ContentSummary[]): { primary: ContentSummary | null; rest: ContentSummary[] } {
  if (contents.length === 0) return { primary: null, rest: [] }
  // 우선순위 유형이 있으면 그 첫 항목을 primary로.
  for (const type of PRIORITY_TYPES) {
    const match = contents.find((c) => c.content_type === type)
    if (match) {
      const rest = contents.filter((c) => c.id !== match.id).slice(0, 3)
      return { primary: match, rest }
    }
  }
  return { primary: contents[0], rest: contents.slice(1, 4) }
}

export function FeaturedContent({ contents, hospitalSlug, hospitalName, directorName }: Props) {
  const { primary, rest } = selectFeatured(contents)
  if (!primary) return null

  const sectionLede = `환자 질문에 답하기 위해 ${hospitalName}에서 먼저 정리한 글입니다.`
  const primaryTypeLabel = TYPE_LABELS[primary.content_type] ?? primary.content_type
  const primaryDate = formatDate(primary.published_at, primary.scheduled_date)
  const primaryImageUrl = resolveAssetUrl(primary.image_url)
  // rest가 비면(콘텐츠가 희소하면) 2열 그리드가 텅 비어 보이므로 단일 컬럼으로 collapse.
  const isSparse = rest.length === 0

  return (
    <section className="clinic-featured" aria-label="대표 의료 정보">
      <div className="clinic-featured-inner">
        <span className="clinic-divider" aria-hidden="true" />
        <header className="clinic-section-header">
          <span className="clinic-section-label">대표 의료 정보</span>
          <h2 className="clinic-section-heading">먼저 정리한 글</h2>
          <p className="clinic-section-lede">{sectionLede}</p>
        </header>

        <div className={`clinic-featured-grid${isSparse ? ' clinic-featured-grid--solo' : ''}`}>
          <Link
            href={`/${hospitalSlug}/contents/${primary.id}`}
            className="clinic-featured-primary"
            aria-label={`대표 콘텐츠 — ${primary.title}`}
          >
            <div className="clinic-featured-primary-image">
              {primaryImageUrl ? (
                <Image
                  src={primaryImageUrl}
                  alt={primary.title}
                  fill
                  sizes="(max-width: 880px) 100vw, 720px"
                  style={{ objectFit: 'cover' }}
                  priority
                  unoptimized={shouldBypassNextImageOptimization(primaryImageUrl)}
                />
              ) : (
                <div
                  className={`clinic-content-card-image clinic-content-card-image--placeholder is-${primary.content_type.toLowerCase()}`}
                  aria-hidden="true"
                  style={{ width: '100%', height: '100%', position: 'absolute', inset: 0 }}
                >
                  <span className="clinic-content-card-image-glyph" aria-hidden="true">
                    <svg
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth={1.6}
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      style={{ width: '100%', height: '100%' }}
                    >
                      <path d="M9 11h6m-6 4h4M6 4h12a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z" />
                    </svg>
                  </span>
                  {primaryTypeLabel}
                </div>
              )}
            </div>
            <div className="clinic-featured-primary-body">
              <span className={`clinic-tag ${categoryTagClass(primary.content_type)}`}>{primaryTypeLabel}</span>
              <h3 className="clinic-featured-primary-title">{primary.title}</h3>
              {primary.meta_description && (
                <p className="clinic-featured-primary-summary">{primary.meta_description}</p>
              )}
              <p className="clinic-featured-primary-meta">
                <strong>{directorName} 원장</strong>
                <span className="clinic-content-card-meta-dot" aria-hidden="true" />
                <span>{primaryDate}</span>
                <span className="clinic-content-card-meta-dot" aria-hidden="true" />
                <span>{primary.reading_minutes ?? 1}분 분량</span>
              </p>
            </div>
          </Link>

          {!isSparse && (
            <div className="clinic-featured-secondary">
              {rest.map((content) => {
                const typeLabel = TYPE_LABELS[content.content_type] ?? content.content_type
                const date = formatDate(content.published_at, content.scheduled_date)
                const imageUrl = resolveAssetUrl(content.image_url)
                return (
                  <Link
                    key={content.id}
                    href={`/${hospitalSlug}/contents/${content.id}`}
                    className="clinic-featured-secondary-card"
                    aria-label={`${typeLabel} — ${content.title}`}
                  >
                    <div className="clinic-featured-secondary-thumb">
                      {imageUrl ? (
                        <Image
                          src={imageUrl}
                          alt={content.title}
                          fill
                          sizes="88px"
                          style={{ objectFit: 'cover' }}
                          unoptimized={shouldBypassNextImageOptimization(imageUrl)}
                        />
                      ) : (
                        <div
                          className={`clinic-content-card-image--placeholder is-${content.content_type.toLowerCase()}`}
                          style={{ width: '100%', height: '100%' }}
                          aria-hidden="true"
                        />
                      )}
                    </div>
                    <div className="clinic-featured-secondary-meta">
                      <span className={`clinic-tag clinic-tag--sm ${categoryTagClass(content.content_type)}`}>{typeLabel}</span>
                      <h4 className="clinic-featured-secondary-title">{content.title}</h4>
                      <p className="clinic-featured-secondary-date">{date} · {content.reading_minutes ?? 1}분</p>
                    </div>
                  </Link>
                )
              })}
              <Link href={`/${hospitalSlug}/contents`} className="clinic-featured-allcard">
                <span className="clinic-featured-allcard-meta">
                  <span className="clinic-featured-allcard-title">의료 정보 전체 보기</span>
                  <span className="clinic-featured-allcard-sub">진료 안내와 건강 정보</span>
                </span>
                <ChevronRightIcon className="clinic-icon" />
              </Link>
            </div>
          )}
        </div>

        {isSparse && (
          <Link href={`/${hospitalSlug}/contents`} className="clinic-featured-more">
            의료 정보 전체 보기
            <ChevronRightIcon className="clinic-icon clinic-icon--sm" style={{ color: 'currentColor' }} />
          </Link>
        )}
      </div>
    </section>
  )
}
