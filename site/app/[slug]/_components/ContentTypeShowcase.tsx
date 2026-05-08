import Link from 'next/link'

import { TYPE_LABELS, type ContentItem } from '@/lib/api'

import { ChevronRightIcon } from './icons'
import { ContentCard } from './ContentCard'

interface Props {
  contents: ContentItem[]
  hospitalSlug: string
  hospitalName: string
}

// 환자가 가장 자주 찾는 순서로 노출. AI 답변 중 인용 가능성도 이 순서를 따른다.
const PRIORITY_TYPES = ['FAQ', 'DISEASE', 'TREATMENT', 'COLUMN', 'HEALTH', 'LOCAL', 'NOTICE']
const MAX_TYPES_SHOWN = 3
const MAX_PER_TYPE = 1

export function ContentTypeShowcase({ contents, hospitalSlug, hospitalName }: Props) {
  if (contents.length === 0) {
    return null
  }

  const grouped = new Map<string, ContentItem[]>()
  for (const content of contents) {
    const list = grouped.get(content.content_type) ?? []
    list.push(content)
    grouped.set(content.content_type, list)
  }

  const orderedTypes = PRIORITY_TYPES.filter((type) => grouped.has(type)).slice(0, MAX_TYPES_SHOWN)
  if (orderedTypes.length === 0) {
    return null
  }

  return (
    <section className="clinic-section clinic-section--surface">
      <div className="clinic-section-inner">
        <header className="clinic-section-header">
          <span className="clinic-section-eyebrow">Patient Questions</span>
          <h2 className="clinic-section-heading">AI 답변에서 자주 등장하는 주제</h2>
          <p className="clinic-section-lede">
            검수된 콘텐츠 중에서 환자 질문에 답할 수 있는 주제를 모았습니다. 전체 의료 정보는 아래
            의료 정보 모아 보기에서 확인하실 수 있습니다.
          </p>
        </header>

        <div className="clinic-showcase">
          {orderedTypes.flatMap((type) => {
            const items = (grouped.get(type) ?? []).slice(0, MAX_PER_TYPE)
            return items.map((content) => (
              <ContentCard
                key={content.id}
                content={content}
                hospitalSlug={hospitalSlug}
                hospitalName={hospitalName}
              />
            ))
          })}
        </div>

        <div style={{ marginTop: '32px', display: 'flex', justifyContent: 'flex-end' }}>
          <Link className="clinic-btn clinic-btn-secondary" href={`/${hospitalSlug}/contents`}>
            전체 의료 정보 보기 ({contents.length}편)
            <ChevronRightIcon className="clinic-icon clinic-icon--sm" style={{ color: 'currentColor' }} />
          </Link>
        </div>
      </div>
    </section>
  )
}

export const SHOWCASE_PRIORITY_TYPES = PRIORITY_TYPES
