import { instrumentSection, measuredFigures } from "@/lib/landing-copy";

/**
 * 히어로 바로 아래 계기판 — 환자가 이미 AI로 병원을 찾는다는 사실 두 개.
 *
 * 앞 버전은 왼쪽 판에 측정 규약(3 × 2 × 3 = 18)을 세웠다. 이 서비스를 사는 사람은
 * 원장님이고, 모델명·반복 횟수는 원장님께 설득 근거가 되지 못해 FAQ로 내렸다.
 *
 * 두 숫자에는 사람 아이콘(10명)을 붙였다. 라운드·그림자 없이 단색만 쓴다. 상호작용이 없으므로 서버 컴포넌트로 둔다.
 */
export default function HeroInstrument() {
  return (
    <section className="instrument" aria-label="환자의 AI 이용 현황" data-reveal>
      <div className="instrument-method">
        <p className="instrument-premise">{instrumentSection.label}</p>
        <p className="instrument-headline">{instrumentSection.lead}</p>
        <p className="instrument-note">{instrumentSection.note}</p>
      </div>

      <dl className="instrument-result">
        {measuredFigures.map((figure) => (
          // 색이 곧 출처의 구분이다 — 인용값(`measured: false`)은 숫자를 잉크로 둔다.
          <div key={figure.value} data-measured={figure.measured ? "true" : "false"}>
            <dt>
              {/* `<dl>` 안의 `<div>`는 dt·dd만 품을 수 있어 그림도 dt 안에 둔다. */}
              <FigureGlyph filled={figure.filled} />
              <span className="figure-value">{figure.value}</span>
            </dt>
            <dd>
              {figure.label}
              <span className="instrument-meaning">{figure.meaning}</span>
              <span className="instrument-source">{figure.source}</span>
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

/** 숫자 옆 그림. 값은 옆의 숫자가 이미 말하므로 보조기기에는 숨긴다. */
function FigureGlyph({ filled }: { filled: number }) {
  return (
    <span className="glyph-people" aria-hidden="true">
      {Array.from({ length: 10 }, (_, i) => (
        <svg
          key={i}
          viewBox="0 0 34 46"
          data-on={i < filled ? "" : undefined}
        >
          <circle cx="17" cy="9" r="8" />
          <path d="M3 44c0-10 6-17 14-17s14 7 14 17z" />
        </svg>
      ))}
    </span>
  );
}
