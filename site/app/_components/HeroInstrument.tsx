import { measuredFigures, measurementSpec } from "@/lib/landing-copy";

/**
 * 히어로 바로 아래 계기판.
 *
 * ## 왜 여기에 데이터가 있어야 하는가
 *
 * 접힘 위가 문장 두 줄과 버튼뿐이면 어느 카테고리 제품인지 보이지 않는다. 첫 화면에
 * **규약과 최근 측정치**가 있으면 같은 카피가 'AI 마케팅'이 아니라 '측정 도구'로
 * 읽힌다. 원장님이 회의적일수록 먼저 확인하는 것은 주장이 아니라 조건이다.
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

      <dl className="instrument-result">
        {measuredFigures.map((figure) => (
          // 색이 곧 출처의 구분이다 — 인용값(`measured: false`)은 파랑을 쓰지 않는다.
          <div key={figure.value} data-measured={figure.measured ? "true" : "false"}>
            <dt>
              <span className="figure-value">{figure.value}</span>
            </dt>
            <dd>
              {figure.label}
              {/* 해석 기준 — 같은 숫자를 사람의 문장으로 다시 말한다. */}
              <span className="instrument-meaning">{figure.meaning}</span>
              <span className="instrument-source">{figure.source}</span>
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
