import Link from 'next/link'
import { formatSeoulDate } from '@/lib/clinic-dates'

import { TYPE_LABELS, type ContentSummary } from '@/lib/api'
import { categoryTagClass } from '@/lib/content-meta'

interface Props {
  content: ContentSummary
  hospitalRootUrl: string
  hospitalName: string
}

export function ContentCard({ content, hospitalRootUrl }: Props) {
  const typeLabel = TYPE_LABELS[content.content_type] ?? content.content_type
  const dateLabel = formatSeoulDate(content.published_at, content.scheduled_date)

  return (
    <Link
      href={`${hospitalRootUrl}/contents/${content.id}`}
      className="clinic-content-card"
      aria-label={`${typeLabel} 콘텐츠 — ${content.title}`}
    >
      <span className={`clinic-tag ${categoryTagClass(content.content_type)}`}>{typeLabel}</span>
      <h3 className="clinic-content-card-title">{content.faq_question || content.title}</h3>
      {content.meta_description && (
        <p className="clinic-content-card-summary">{content.meta_description}</p>
      )}
      <p className="clinic-content-card-meta">
        <span>{dateLabel}</span>
        <span className="clinic-content-card-meta-dot" aria-hidden="true" />
        {/* 목록 응답은 body를 생략하므로 서버 계산값(reading_minutes)을 사용한다. */}
        <span>{content.reading_minutes ?? 1}분 분량</span>
      </p>
    </Link>
  )
}
