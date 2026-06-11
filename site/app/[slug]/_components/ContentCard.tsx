import Image from 'next/image'
import Link from 'next/link'

import { resolveAssetUrl, TYPE_LABELS, type ContentSummary } from '@/lib/api'
import { categoryTagClass } from '@/lib/content-meta'
import { shouldBypassNextImageOptimization } from '@/lib/image-policy'

interface Props {
  content: ContentSummary
  hospitalSlug: string
  hospitalName: string
}

const VARIANT_BY_TYPE: Record<string, string> = {
  FAQ: 'clinic-content-card--faq',
  COLUMN: 'clinic-content-card--column',
}

const TYPE_GLYPHS: Record<string, JSX.Element> = {
  FAQ: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
      <path d="M9.1 9a3 3 0 0 1 5.8 1c0 2-3 3-3 3" />
      <line x1="12" y1="17" x2="12" y2="17.01" />
    </svg>
  ),
  DISEASE: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 2v20M2 12h20" />
      <circle cx="12" cy="12" r="10" />
    </svg>
  ),
  TREATMENT: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round">
      <path d="m17 2 5 5" />
      <path d="m13 6 5 5" />
      <path d="M14 7 7 14a3 3 0 0 0 0 4l-3 3 1 1 3-3a3 3 0 0 0 4 0l7-7z" />
    </svg>
  ),
  COLUMN: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round">
      <path d="M4 4h12a4 4 0 0 1 4 4v12H8a4 4 0 0 1-4-4z" />
      <line x1="8" y1="9" x2="16" y2="9" />
      <line x1="8" y1="13" x2="14" y2="13" />
    </svg>
  ),
  HEALTH: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 2 4 6v6c0 5 3 9 8 10 5-1 8-5 8-10V6z" />
      <path d="m9 12 2 2 4-4" />
    </svg>
  ),
  LOCAL: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z" />
      <circle cx="12" cy="10" r="3" />
    </svg>
  ),
  NOTICE: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 11l18-7-7 18-2-8z" />
    </svg>
  ),
}

function formatDate(value: string | null | undefined, fallback: string) {
  if (!value) return fallback
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return fallback
  return parsed.toLocaleDateString('ko-KR', { year: 'numeric', month: 'long', day: 'numeric' })
}

export function ContentCard({ content, hospitalSlug }: Props) {
  const variant = VARIANT_BY_TYPE[content.content_type] ?? ''
  const typeLabel = TYPE_LABELS[content.content_type] ?? content.content_type
  const typeKey = content.content_type.toLowerCase()
  const dateLabel = formatDate(content.published_at, content.scheduled_date)
  const glyph = TYPE_GLYPHS[content.content_type]
  const imageUrl = resolveAssetUrl(content.image_url)

  return (
    <Link
      href={`/${hospitalSlug}/contents/${content.id}`}
      className={`clinic-content-card ${variant}`.trim()}
      aria-label={`${typeLabel} 콘텐츠 — ${content.title}`}
    >
      {imageUrl ? (
        <div className="clinic-content-card-image">
          <Image
            src={imageUrl}
            alt={content.title}
            fill
            sizes="(max-width: 720px) 100vw, 360px"
            style={{ objectFit: 'cover' }}
            unoptimized={shouldBypassNextImageOptimization(imageUrl)}
          />
        </div>
      ) : (
        <div
          className={`clinic-content-card-image clinic-content-card-image--placeholder is-${typeKey}`}
          aria-hidden="true"
        >
          {glyph && (
            <span className="clinic-content-card-image-glyph" style={{ color: 'currentColor' }}>
              {glyph}
            </span>
          )}
          <span style={{ fontSize: 12, fontWeight: 700, letterSpacing: '0.06em' }}>
            {typeLabel.toUpperCase()}
          </span>
        </div>
      )}
      <div className="clinic-content-card-body">
        <span className={`clinic-tag ${categoryTagClass(content.content_type)}`}>{typeLabel}</span>
        <h3 className="clinic-content-card-title">{content.title}</h3>
        {content.meta_description && (
          <p className="clinic-content-card-summary">{content.meta_description}</p>
        )}
        <p className="clinic-content-card-meta">
          <span>{dateLabel}</span>
          <span className="clinic-content-card-meta-dot" aria-hidden="true" />
          {/* 목록 응답은 body를 생략하므로 서버 계산값(reading_minutes)을 사용한다. */}
          <span>{content.reading_minutes ?? 1}분 분량</span>
        </p>
      </div>
    </Link>
  )
}
