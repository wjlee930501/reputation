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
 * ## 서버 컴포넌트다
 *
 * 상호작용이 없다. 클라이언트로 내리면 첫 화면의 데이터가 하이드레이션 뒤에 뜨는데,
 * 그러면 이 블록이 존재하는 이유(첫인상)를 스스로 없애는 셈이다.
 *
 * 등장 애니메이션은 `.figure-value` 마스크를 그대로 쓴다 — 페이지의 다른 큰 숫자와
 * 같은 방식으로 올라와야 같은 종류의 값으로 읽힌다.
 */
export default function HeroInstrument() {
  return (
    <section className="instrument" aria-label="측정 규약과 최근 실측" data-reveal>
      <dl className="instrument-protocol">
        {measurementSpec.protocol.map((row) => (
          <div key={row.key}>
            <dt>{row.key}</dt>
            <dd>
              <span className="instrument-value">{row.value}</span>
              <span className="instrument-note">{row.note}</span>
            </dd>
          </div>
        ))}
      </dl>

      <dl className="instrument-result">
        {measuredFigures.map((figure) => (
          <div key={figure.value}>
            <dt>
              <span className="figure-value">{figure.value}</span>
            </dt>
            <dd>
              {figure.label}
              <span className="instrument-source">{figure.source}</span>
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
