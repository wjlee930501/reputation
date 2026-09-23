'use client'

import { useState } from 'react'

import BrochureGate from './BrochureGate'
import BrochureViewer from './BrochureViewer'

const CHAPTERS = [
  { no: '01', title: '검색의 변화', pages: '2–4쪽', note: '환자가 병원을 찾는 첫 질문이 AI로 옮겨 가는 흐름' },
  { no: '02', title: '서비스', pages: '5–7쪽', note: 'Re:putation이 하는 일과 매달 도는 운영 방식' },
  { no: '03', title: '측정과 진단', pages: '8–9쪽', note: '같은 질문을 18번 묻는 측정 방식과 진단 리포트' },
  { no: '04', title: '도입', pages: '10–12쪽', note: '도입 절차와 요금제' },
]

/**
 * 게이트 → 뷰어 전환을 한 화면에서 처리한다. 서버가 열람 쿠키를 읽어 `initialToken`을
 * 넘기면 게이트 없이 바로 뷰어부터 보인다(다시 찾아온 원장).
 */
export default function BrochureExperience({ initialToken }: { initialToken: string | null }) {
  const [token, setToken] = useState<string | null>(initialToken)
  const [justUnlocked, setJustUnlocked] = useState(false)

  if (token) {
    return (
      <section className="brochure-section brochure-section--view" aria-labelledby="brochure-heading">
        <div className="brochure-inner">
          <div className="brochure-view-head">
            <p className="section-label">서비스 소개서</p>
            <h1 id="brochure-heading">Re:putation 서비스 소개서</h1>
          </div>
          <BrochureViewer token={token} viewer="owner" autoOpen={justUnlocked} />
        </div>
      </section>
    )
  }

  return (
    <section className="brochure-section" aria-labelledby="brochure-heading">
      <div className="brochure-inner brochure-grid">
        <div className="brochure-intro">
          <p className="section-label">서비스 소개서</p>
          <h1 id="brochure-heading">
            Re:putation을
            <br />
            <em className="accent">12페이지</em>로 먼저 살펴보세요
          </h1>
          <p className="brochure-lede">
            AI 답변 속 병원 추천 구조부터 운영 방식, 측정 방법, 요금까지 정리했습니다. 약 3분이면
            읽으실 수 있습니다.
          </p>
          <ol className="brochure-toc">
            {CHAPTERS.map((chapter) => (
              <li key={chapter.no}>
                <span className="brochure-toc-no">{chapter.no}</span>
                <div>
                  <p className="brochure-toc-title">
                    {chapter.title}
                    <span>{chapter.pages}</span>
                  </p>
                  <p className="brochure-toc-note">{chapter.note}</p>
                </div>
              </li>
            ))}
          </ol>
        </div>

        <BrochureGate
          onUnlocked={(next) => {
            setJustUnlocked(true)
            setToken(next)
            window.scrollTo({ top: 0 })
          }}
        />
      </div>
    </section>
  )
}
