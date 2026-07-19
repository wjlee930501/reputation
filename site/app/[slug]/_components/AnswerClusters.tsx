import Link from 'next/link'

import { TYPE_LABELS, type ContentSummary } from '@/lib/api'
import { categoryTagClass } from '@/lib/content-meta'

import { ChevronRightIcon, MessageIcon } from './icons'

interface Props {
  contents: ContentSummary[]
  hospitalRootUrl: string
  treatments: Array<{ name: string; description: string }>
  region: string[]
  specialties: string[]
}

// 환자가 실제로 던지는 질문 형태를 우선 노출한다(질문이 주인공). FAQ를 먼저,
// 이후 질환·치료·건강 정보 순으로 채워 "큰 활자 질문 리스트"를 구성한다.
const QUESTION_PRIORITY = ['FAQ', 'DISEASE', 'TREATMENT', 'HEALTH', 'LOCAL', 'COLUMN', 'NOTICE']

function selectQuestions(contents: ContentSummary[], limit: number): ContentSummary[] {
  const byPriority = [...contents].sort((a, b) => {
    const ai = QUESTION_PRIORITY.indexOf(a.content_type)
    const bi = QUESTION_PRIORITY.indexOf(b.content_type)
    return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi)
  })
  return byPriority.slice(0, limit)
}

export function AnswerClusters({ contents, hospitalRootUrl, treatments, region, specialties }: Props) {
  const questions = selectQuestions(contents, 6)
  if (questions.length === 0) return null

  const regionText = region.length > 0 ? region.join(' ') : '내원 지역'
  const specialtyText = specialties.length > 0 ? specialties.slice(0, 3).join(', ') : '진료 영역'

  // 상위 3개는 답변 요약까지 크게, 나머지는 질문만 컴팩트하게(FAQ에 카테고리 데이터가 없어 탭 대신 밀도로 구분).
  const primary = questions.slice(0, 3)
  const compact = questions.slice(3)

  return (
    <section id="answer-clusters" className="clinic-section clinic-section--answers">
      <div className="clinic-section-inner">
        <div className="clinic-answers-layout">
          <aside className="clinic-answers-rail">
            <span className="clinic-answers-eyebrow">진료 전 확인</span>
            <h2 className="clinic-section-title">자주 확인하는 질문</h2>
            <p className="clinic-section-note">
              {regionText}에서 {specialtyText} 진료를 찾을 때 환자분들이 자주 묻는 질문을 모았습니다.
            </p>
            <div className="clinic-answers-hint">
              <span className="clinic-answers-hint-icon" aria-hidden="true"><MessageIcon /></span>
              <div>
                <strong>처음 오시나요?</strong>
                <p>진료 전 확인하면 좋은 질문부터 살펴보세요. 개인별 판단은 진료 상담에서 확인합니다.</p>
              </div>
            </div>
            <Link href={`${hospitalRootUrl}/contents`} className="clinic-answers-all">
              의료 정보 전체 보기
              <ChevronRightIcon className="clinic-icon clinic-icon--sm" style={{ color: 'currentColor' }} />
            </Link>
          </aside>

          <div className="clinic-answers-main">
            <ol className="clinic-qa-list" aria-label="자주 묻는 질문">
              {primary.map((content) => {
                const typeLabel = TYPE_LABELS[content.content_type] ?? content.content_type
                const question = content.faq_question || content.title
                return (
                  <li key={content.id} className="clinic-qa-item">
                    <Link href={`${hospitalRootUrl}/contents/${content.id}`} className="clinic-qa-link">
                      <span className="clinic-qa-main">
                        <span className={`clinic-tag clinic-tag--sm ${categoryTagClass(content.content_type)}`}>
                          {typeLabel}
                        </span>
                        <span className="clinic-qa-q">{question}</span>
                        {content.faq_answer_summary || content.meta_description ? (
                          <span className="clinic-qa-a">
                            {content.faq_answer_summary || content.meta_description}
                          </span>
                        ) : null}
                      </span>
                      <ChevronRightIcon className="clinic-icon clinic-qa-arrow" aria-hidden="true" />
                    </Link>
                  </li>
                )
              })}
            </ol>

            {compact.length > 0 && (
              <ol className="clinic-qa-compact" aria-label="추가 질문">
                {compact.map((content) => {
                  const question = content.faq_question || content.title
                  return (
                    <li key={content.id}>
                      <Link href={`${hospitalRootUrl}/contents/${content.id}`} className="clinic-qa-compact-row">
                        <span className="clinic-qa-compact-q">{question}</span>
                        <ChevronRightIcon className="clinic-icon clinic-icon--sm clinic-qa-compact-arrow" aria-hidden="true" />
                      </Link>
                    </li>
                  )
                })}
              </ol>
            )}

            {treatments.length > 0 && (
              <div className="clinic-answer-treatment-strip" aria-label="주요 진료 영역">
                {treatments.slice(0, 6).map((treatment) => (
                  <Link key={treatment.name} href={`${hospitalRootUrl}/treatments`}>
                    {treatment.name}
                  </Link>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </section>
  )
}
