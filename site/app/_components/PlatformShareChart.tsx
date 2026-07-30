import type { PlatformShare } from "@/lib/landing-copy";

/**
 * 국내 AI 채널 점유율 막대 — **넛지가 목적인 차트다.**
 *
 * 측정 대상(ChatGPT·Gemini)만 accent로 칠하고 나머지는 회색으로 둔다. 그러면 "두 곳만
 * 재는 이유"를 문장으로 설명하기 전에 그림에서 먼저 읽힌다 — 칠해진 면적이 84%다.
 *
 * 라이브러리를 쓰지 않는다. 막대 4개에 차트 런타임을 들이면 번들만 커지고, 서버 컴포넌트로
 * 렌더할 수 없어 첫 페인트도 늦어진다.
 *
 * 접근성: 막대는 `aria-hidden`이고 같은 데이터를 표로 함께 낸다. 스크린리더는 표를 읽는다 —
 * SVG에 aria-label을 붙여 수치를 문장으로 늘어놓는 방식보다 훨씬 읽기 쉽다.
 */
export default function PlatformShareChart({
  shares,
  sourceNote,
}: {
  shares: PlatformShare[];
  sourceNote: string;
}) {
  const measuredTotal = shares
    .filter((item) => item.measured)
    .reduce((sum, item) => sum + item.share, 0);

  return (
    <figure className="share-chart">
      <div className="share-bars" aria-hidden="true">
        {shares.map((item) => (
          <div key={item.name} className="share-row">
            <span className="share-name">{item.name}</span>
            <span className="share-track">
              <span
                className={item.measured ? "share-fill is-measured" : "share-fill"}
                style={{ width: `${item.share}%` }}
              />
            </span>
            <span className="share-value">{item.share}%</span>
          </div>
        ))}
      </div>

      <p className="share-total">
        <strong>{measuredTotal.toFixed(1)}%</strong>
        <span>Re:putation이 측정하는 두 곳의 합</span>
      </p>

      {/* 스크린리더용 정본. 시각적으로는 숨기지 않고 접어서 둔다 — 숫자를 확인하려는
          사용자에게도 필요한 표다. */}
      <details className="share-table">
        <summary>수치 표로 보기</summary>
        <table>
          <caption>{sourceNote}</caption>
          <thead>
            <tr>
              <th scope="col">AI 서비스</th>
              <th scope="col">웹 검색 점유율</th>
              <th scope="col">진단 대상</th>
            </tr>
          </thead>
          <tbody>
            {shares.map((item) => (
              <tr key={item.name}>
                <th scope="row">{item.name}</th>
                <td>{item.share}%</td>
                <td>{item.measured ? "예" : "아니오"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>

      <figcaption className="share-source">{sourceNote}</figcaption>
    </figure>
  );
}
