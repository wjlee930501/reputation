import Link from 'next/link'

import type { FaqEntry } from '@/lib/schema'

import { ChevronRightIcon } from './icons'

interface Props {
  entries: FaqEntry[]
  hospitalRootUrl: string
}

/**
 * 승인된 FAQ 질문과 답변 요약을 화면에 그린다 (P-A-5).
 *
 * 홈은 FAQPage 구조화 데이터를 내보내면서도 그 질문과 답변을 화면에는 한 줄도
 * 싣지 않았다. 콘텐츠가 적은 병원(질문 목록 섹션이 꺼지는 조건)에서는 페이지에
 * 없는 Q&A만 마크업으로 주장하는 상태였다. 여기서 그리는 항목과 JSON-LD의
 * mainEntity는 같은 `selectFaqEntries()` 결과다.
 */
export function ClinicFaq({ entries, hospitalRootUrl }: Props) {
  if (entries.length === 0) return null

  return (
    <section id="faq" className="clinic-section">
      <div className="clinic-section-inner">
        <header className="clinic-section-head">
          <h2 className="clinic-section-title">자주 묻는 질문과 답변 요약</h2>
          <p className="clinic-section-note">
            진료실에서 반복되는 질문에 대한 답변 요약입니다. 개인의 상태에 따라 판단이
            달라질 수 있으니 자세한 내용은 진료 상담에서 확인해 주세요.
          </p>
        </header>

        <dl className="clinic-faq-list">
          {entries.map((entry) => (
            <div key={entry.id} className="clinic-faq-item">
              <dt className="clinic-faq-question">{entry.question}</dt>
              <dd className="clinic-faq-answer">
                <p>{entry.answer}</p>
                <Link href={entry.url} className="clinic-faq-more">
                  자세히 보기
                  <ChevronRightIcon className="clinic-icon clinic-icon--sm" style={{ color: 'currentColor' }} />
                </Link>
              </dd>
            </div>
          ))}
        </dl>

        <Link href={`${hospitalRootUrl}/contents`} className="clinic-faq-all">
          의료 정보 전체 보기
          <ChevronRightIcon className="clinic-icon clinic-icon--sm" style={{ color: 'currentColor' }} />
        </Link>
      </div>
    </section>
  )
}
