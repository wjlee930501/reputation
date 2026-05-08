import Image from 'next/image'
import Link from 'next/link'

import { TYPE_LABELS, type ContentItem } from '@/lib/api'

interface Props {
  content: ContentItem
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

export function ContentCard({ content, hospitalSlug, hospitalName }: Props) {
  const variant = VARIANT_BY_TYPE[content.content_type] ?? ''
  const typeLabel = TYPE_LABELS[content.content_type] ?? content.content_type
  const dateLabel = formatDate(content.published_at, content.scheduled_date)

  return (
    <Link
      href={`/${hospitalSlug}/contents/${content.id}`}
      className={`clinic-content-card ${variant}`.trim()}
      aria-label={`${typeLabel} 콘텐츠 — ${content.title}`}
    >
      {content.image_url ? (
        <div className="clinic-content-card-image">
          <Image
            src={content.image_url}
            alt={content.title}
            fill
            sizes="(max-width: 720px) 100vw, 360px"
            style={{ objectFit: 'cover' }}
          />
        </div>
      ) : (
        <div className="clinic-content-card-image clinic-content-card-image--placeholder" aria-hidden="true">
          {hospitalName}
        </div>
      )}
      <div className="clinic-content-card-body">
        <span className="clinic-content-card-type">{typeLabel}</span>
        <h3 className="clinic-content-card-title">{content.title}</h3>
        {content.meta_description && (
          <p className="clinic-content-card-summary">{content.meta_description}</p>
        )}
        <p className="clinic-content-card-meta">{dateLabel}</p>
      </div>
    </Link>
  )
}
