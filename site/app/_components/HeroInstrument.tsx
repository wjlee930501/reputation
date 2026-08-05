import {
  answerDemo,
  answerExamples,
  measurementSpec,
  previewSection,
} from "@/lib/landing-copy";

import DiagnosisPreview from "./DiagnosisPreview";

/**
 * 히어로 바로 아래 계기판.
 *
 * ## 왜 여기에 산출물이 있어야 하는가
 *
 * 접힘 위가 문장 두 줄과 버튼뿐이면 어느 카테고리 제품인지 보이지 않는다. 첫 화면에
 * **받는 것의 실물과 그것을 만든 규약**이 나란히 있으면 같은 카피가 'AI 마케팅'이
 * 아니라 '측정 도구'로 읽힌다. 원장님이 회의적일수록 먼저 확인하는 것은 주장이 아니라
 * 조건이고, 그다음이 "그래서 뭘 받는가"다.
 *
 * 외부 조사 수치(78.1% · 60%)는 여기 있었고 `#numbers`로 옮겼다 — "왜 재야 하는가"는
 * 그 섹션의 질문이고, 이 자리는 "무엇을 받는가"가 맡는다.
 *
 * ## 파라미터 표에서 논증으로
 *
 * 앞 버전은 `측정 규약 / 고정 표본 / 측정 대상` 3행 표였다. 원장에게 `gemini-3.6-flash`는
 * 아무 의미가 없고, 12~13px 표로 깔려 있어 각주처럼 읽혔다. 정보는 유일한데 포장이
 * 틀렸던 것이다.
 *
 * 지금은 세 가지 문법을 지킨다:
 *   ① 논증이 숫자보다 먼저 온다 (Evertune)
 *   ② 헤드라인 문장에 숫자는 하나, 파라미터는 각주로 (Consumer Reports)
 *   ③ 큰 숫자에는 "그래서 이게 큰 거냐"를 붙인다 (Cochrane)
 *
 * ## 서버 컴포넌트다
 *
 * 상호작용이 없다. 클라이언트로 내리면 첫 화면의 데이터가 하이드레이션 뒤에 뜨는데,
 * 그러면 이 블록이 존재하는 이유(첫인상)를 스스로 없애는 셈이다.
 */
export default function HeroInstrument() {
  return (
    <section className="instrument" aria-label="측정 방법과 최근 실측" data-reveal>
      {/* **받는 것의 실물을 접힘 위에 둔다.**
          앞 버전은 여기가 외부 조사 수치(78.1% · 60%) 둘이었다. 그 숫자들은 "왜 재야
          하는가"에는 답했지만 "무엇을 받는가"에는 답하지 않았고, 첫 화면에 산출물이
          없으니 히어로 한 줄이 혼자 설득해야 했다 — 외부 검토에서 "이름을 세어 숫자를
          알려주는 것 같다"는 말이 나온 지점이다.

          `#preview` 섹션과 같은 컴포넌트를 쓴다. 미리보기와 실물이 다르면 측정 규율을
          파는 페이지에서 첫 번째로 깨지는 것이 그 규율이다. */}
      <div className="instrument-report">
        <p className="instrument-report-label">{previewSection.label}</p>
        <DiagnosisPreview example={answerExamples[0]} disclaimer={answerDemo.disclaimer} />
      </div>

      <div className="instrument-method">
        {/* 전제 — 이 문장이 없으면 아래 "9번씩"이 왜 필요한지 설명되지 않는다. */}
        <p className="instrument-premise">{measurementSpec.premise}</p>
        <p className="instrument-headline">{measurementSpec.headline}</p>

        <p className="instrument-spec">
          {measurementSpec.spec}
          <span>{measurementSpec.models}</span>
        </p>

        {/* 방법론을 공개하는 목적. 이게 없으면 위 파라미터는 장식이다. */}
        <p className="instrument-repro">{measurementSpec.reproducibility}</p>
      </div>

    </section>
  );
}
