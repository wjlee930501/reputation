import type { Metadata } from 'next'
import DiagnosisForm from './DiagnosisForm'

// 이 페이지는 개인 신청 표면이다 — 색인 대상이 아니다.
// (병원 콘텐츠 허브는 정반대로 색인되어야 한다. 두 표면을 섞지 않는다.)
export const metadata: Metadata = {
  title: '우리 병원 AI 노출 현황 진단하기 | Re:putation',
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
        <h1>우리 병원 AI 노출 현황 진단하기</h1>
        <p className="dg-lede">
          환자가 ChatGPT나 Gemini에 “우리 동네 어느 병원이 좋아?”라고 물었을 때,
          우리 병원 이름이 나오는지 <strong>실제로 물어보고</strong> 알려드립니다.
        </p>
      </header>

      <section className="dg-how">
        <h2>어떻게 재나요</h2>
        <ol>
          <li>
            입력하신 <strong>지역·진료과·키워드</strong>로 환자가 쓸 법한 질문 3개를 만듭니다.
          </li>
          <li>
            그 질문을{' '}
            <strong>
              <span className="dg-no-break">ChatGPT(OpenAI API)</span>와{' '}
              <span className="dg-no-break">Gemini API</span>에 각각 3번씩
            </strong>{' '}
            던집니다.
          </li>
          <li>
            답변에 병원 이름이 나왔는지 세어, <strong>18번 중 몇 번</strong> 나왔는지 알려드립니다.
          </li>
        </ol>
        <p className="dg-note">
          질문에는 <strong>병원 이름을 넣지 않습니다.</strong> 자기 이름을 물으면 언급은 보장되고
          측정은 아무 의미가 없어지기 때문입니다. 사용한 질문 원문·모델명·지시문은 리포트에
          그대로 공개하므로 직접 재현하실 수 있습니다.
        </p>
      </section>

      <section className="dg-form-section" id="apply">
        <h2>진단 신청</h2>
        <DiagnosisForm />
      </section>
    </main>
  )
}
