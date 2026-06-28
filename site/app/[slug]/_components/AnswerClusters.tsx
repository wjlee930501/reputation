import Link from 'next/link'

import { TYPE_LABELS, type ContentSummary } from '@/lib/api'

interface Props {
  contents: ContentSummary[]
  hospitalSlug: string
  treatments: Array<{ name: string; description: string }>
  region: string[]
  specialties: string[]
}

const CLUSTERS = [
  { key: 'faq', label: '자주 묻는 질문', types: ['FAQ'] },
  { key: 'symptom', label: '증상과 질환', types: ['DISEASE', 'HEALTH'] },
  { key: 'treatment', label: '치료와 시술', types: ['TREATMENT'] },
  { key: 'local', label: '지역과 방문', types: ['LOCAL', 'NOTICE'] },
]

export function AnswerClusters({ contents, hospitalSlug, treatments, region, specialties }: Props) {
  const regionText = region.length > 0 ? region.join(' ') : '내원 지역'
  const specialtyText = specialties.length > 0 ? specialties.slice(0, 3).join(', ') : '진료 영역'

  return (
    <section id="answer-clusters" className="clinic-section clinic-section--answers">
      <div className="clinic-section-inner">
        <header className="clinic-section-header">
          <span className="clinic-section-eyebrow">자주 찾는 질문</span>
          <h2 className="clinic-section-heading">진료 전에 자주 확인하는 질문</h2>
          <p className="clinic-section-lede">
            {regionText}에서 {specialtyText} 진료를 찾을 때 환자분들이 자주 묻는 내용을 주제별로 모았습니다.
          </p>
        </header>

        <div className="clinic-answer-grid">
          {CLUSTERS.map((cluster) => {
            const matches = contents
              .filter((content) => cluster.types.includes(content.content_type))
              .slice(0, 3)
            // 빈 비-local 클러스터는 내용 없는 fallback 카피 대신 렌더하지 않는다(slop 제거).
            // local은 콘텐츠가 없어도 방문 안내 링크라는 실질 정보를 제공하므로 유지.
            if (matches.length === 0 && cluster.key !== 'local') return null
            return (
              <article key={cluster.key} className="clinic-answer-card">
                <span className="clinic-answer-label">{cluster.label}</span>
                {matches.length > 0 ? (
                  <ul className="clinic-answer-list">
                    {matches.map((content) => (
                      <li key={content.id}>
                        <Link href={`/${hospitalSlug}/contents/${content.id}`}>
                          <span>{TYPE_LABELS[content.content_type] ?? content.content_type}</span>
                          <strong>{content.faq_question || content.title}</strong>
                        </Link>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <ul className="clinic-answer-list">
                    <li>
                      <Link href={`/${hospitalSlug}/visit`}>
                        <span>방문 안내</span>
                        <strong>오시는 길과 진료시간 확인하기</strong>
                      </Link>
                    </li>
                  </ul>
                )}
              </article>
            )
          })}
        </div>

        {treatments.length > 0 && (
          <div className="clinic-answer-treatment-strip" aria-label="주요 진료 영역">
            {treatments.slice(0, 6).map((treatment) => (
              <Link key={treatment.name} href={`/${hospitalSlug}/treatments`}>
                {treatment.name}
              </Link>
            ))}
          </div>
        )}
      </div>
    </section>
  )
}
