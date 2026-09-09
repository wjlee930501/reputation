import type { Metadata } from 'next'
import DiagnosisForm from './DiagnosisForm'

// 이 페이지는 개인 신청 표면이다 — 색인 대상이 아니다.
// (병원 콘텐츠 허브는 정반대로 색인되어야 한다. 두 표면을 섞지 않는다.)
export const metadata: Metadata = {
  title: '우리 병원 AI 노출 현황 진단받기 | Re:putation',
  description:
    'ChatGPT·Gemini가 우리 지역 진료과 질문에 답할 때 우리 병원이 언급되는지 실제로 측정해 드립니다.',
  robots: { index: false, follow: false },
}

// 남은 자리 카운터가 실시간이어야 하므로 정적 생성하지 않는다.
export const dynamic = 'force-dynamic'

export default function AiDiagnosisPage() {
  return (
    <main id="main-content" className="dg-page">
      <header className="dg-header">
        <p className="dg-eyebrow">무료 AI 노출 진단</p>
        <h1>우리 병원 AI 노출 현황 진단받기</h1>
        <p className="dg-lede">
          환자가 ChatGPT나 Gemini에 “우리 동네 어느 병원이 좋아?”라고 물었을 때
          우리 병원 이름이 나오는지 <strong>분석한 리포트</strong>를 보내드립니다.
        </p>
      </header>

      <section className="dg-form-section" id="apply">
        <h2>진단 신청</h2>
        <DiagnosisForm />
      </section>
    </main>
  )
}
