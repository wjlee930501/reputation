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
