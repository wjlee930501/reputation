import Image from 'next/image'
import Link from 'next/link'

import { TYPE_LABELS, type ContentItem } from '@/lib/api'
import { shouldBypassNextImageOptimization } from '@/lib/image-policy'

import { ChevronRightIcon } from './icons'

interface Props {
  contents: ContentItem[]
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

function selectFeatured(contents: ContentItem[]): { primary: ContentItem | null; rest: ContentItem[] } {
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

  return (
    <section className="clinic-featured" aria-label="대표 의료 콘텐츠">
      <div className="clinic-featured-inner">
        <span className="clinic-divider" aria-hidden="true" />
        <header className="clinic-section-header">
          <span className="clinic-section-eyebrow">Featured</span>
          <h2 className="clinic-section-heading">대표 콘텐츠</h2>
          <p className="clinic-section-lede">{sectionLede}</p>
        </header>

        <div className="clinic-featured-grid">
          <Link
            href={`/${hospitalSlug}/contents/${primary.id}`}
            className="clinic-featured-primary"
            aria-label={`대표 콘텐츠 — ${primary.title}`}
          >
            <div className="clinic-featured-primary-image">
              {primary.image_url ? (
                <Image
                  src={primary.image_url}
                  alt={primary.title}
                  fill
                  sizes="(max-width: 880px) 100vw, 720px"
                  style={{ objectFit: 'cover' }}
                  priority
                  unoptimized={shouldBypassNextImageOptimization(primary.image_url)}
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
              <span className="clinic-featured-primary-eyebrow">{primaryTypeLabel}</span>
              <h3 className="clinic-featured-primary-title">{primary.title}</h3>
              {primary.meta_description && (
                <p className="clinic-featured-primary-summary">{primary.meta_description}</p>
              )}
              <p className="clinic-featured-primary-meta">
                <strong>{directorName} 원장</strong>
                <span>·</span>
                <span>{primaryDate}</span>
                <span>·</span>
                <span style={{ color: 'var(--color-revisit-primary-40)', fontWeight: 600 }}>
                  자세히 보기 →
                </span>
              </p>
            </div>
          </Link>

          <div className="clinic-featured-secondary">
            {rest.map((content) => {
              const typeLabel = TYPE_LABELS[content.content_type] ?? content.content_type
              const date = formatDate(content.published_at, content.scheduled_date)
              return (
                <Link
                  key={content.id}
                  href={`/${hospitalSlug}/contents/${content.id}`}
                  className="clinic-featured-secondary-card"
                  aria-label={`${typeLabel} — ${content.title}`}
                >
                  <div className="clinic-featured-secondary-thumb">
                    {content.image_url ? (
                      <Image
                        src={content.image_url}
                        alt={content.title}
                        fill
                        sizes="88px"
                        style={{ objectFit: 'cover' }}
                        unoptimized={shouldBypassNextImageOptimization(content.image_url)}
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
                    <span className="clinic-featured-secondary-eyebrow">{typeLabel}</span>
                    <h4 className="clinic-featured-secondary-title">{content.title}</h4>
                    <p className="clinic-featured-secondary-date">{date}</p>
                  </div>
                </Link>
              )
            })}
            <Link
              href={`/${hospitalSlug}/contents`}
              className="clinic-featured-secondary-card"
              style={{ alignItems: 'center', justifyContent: 'space-between', borderStyle: 'dashed' }}
            >
              <div className="clinic-featured-secondary-meta">
                <h4 className="clinic-featured-secondary-title" style={{ marginBottom: 2 }}>
                  의료 콘텐츠 전체 보기
                </h4>
                <p className="clinic-featured-secondary-date">{contents.length}편 모음</p>
              </div>
              <ChevronRightIcon className="clinic-icon" />
            </Link>
          </div>
        </div>
      </div>
    </section>
  )
}
