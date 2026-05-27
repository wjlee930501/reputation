import Link from 'next/link'

import { TYPE_LABELS, type ContentItem } from '@/lib/api'

interface Props {
  contents: ContentItem[]
  hospitalSlug: string
  treatments: Array<{ name: string; description: string }>
  region: string[]
  specialties: string[]
}

const CLUSTERS = [
  {
    key: 'faq',
    label: '자주 묻는 질문',
    types: ['FAQ'],
    fallback: '환자가 진료 전 가장 먼저 확인하는 질문을 정리합니다.',
  },
  {
    key: 'symptom',
    label: '증상과 질환',
    types: ['DISEASE', 'HEALTH'],
    fallback: '증상, 질환, 생활 관리 정보를 의료진 검수 콘텐츠로 연결합니다.',
  },
  {
    key: 'treatment',
    label: '치료와 시술',
    types: ['TREATMENT'],
    fallback: '치료 선택 전 확인할 진료 영역별 기본 정보를 안내합니다.',
  },
  {
    key: 'local',
    label: '지역과 방문',
    types: ['LOCAL', 'NOTICE'],
    fallback: '지역, 예약, 내원 전 확인 정보를 같은 맥락에서 제공합니다.',
  },
]

export function AnswerClusters({ contents, hospitalSlug, treatments, region, specialties }: Props) {
  const regionText = region.length > 0 ? region.join(' ') : '내원 지역'
  const specialtyText = specialties.length > 0 ? specialties.slice(0, 3).join(', ') : '진료 영역'

  return (
    <section id="answer-clusters" className="clinic-section clinic-section--answers">
      <div className="clinic-section-inner">
        <header className="clinic-section-header">
          <span className="clinic-section-eyebrow">질문 클러스터</span>
          <h2 className="clinic-section-heading">AI 검색에 걸리기 쉬운 대표 질문 구조</h2>
          <p className="clinic-section-lede">
            {regionText}에서 {specialtyText} 정보를 찾는 환자 질문을 유형별로 묶어, 검색 결과와 AI 답변이 병원 정보를 더 쉽게 연결하도록 구성합니다.
          </p>
        </header>

        <div className="clinic-answer-grid">
          {CLUSTERS.map((cluster) => {
            const matches = contents
              .filter((content) => cluster.types.includes(content.content_type))
              .slice(0, 3)
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
                  <p className="clinic-answer-empty">{cluster.fallback}</p>
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
