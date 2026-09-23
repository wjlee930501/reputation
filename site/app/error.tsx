'use client'

// 공개 표면 에러 경계 — 백엔드 일시 장애(ISR 렌더 중 5xx 등)가 Next 기본 에러 화면
// 대신 브랜드된 안내로 노출되도록 한다. 에러 메시지 원문은 노출하지 않는다.
//
// 병원 공개 페이지(`/[slug]` 이하)도 이 경계를 쓴다. 그래서 404와 같은 이유로
// Re:putation 헤더·CTA 없이 중립 화면으로 둔다(`app/not-found.tsx` 참고).
export default function SiteError({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <main id="main-content" className="landing-shell landing-edge landing-sub">
      <section className="edge-message">
        <p className="edge-message-code">일시적인 오류</p>
        <h1>페이지를 불러오지 못했습니다</h1>
        <p>일시적인 문제로 정보를 가져오지 못했습니다. 잠시 후 다시 시도해 주세요.</p>
        <button type="button" className="btn edge-message-action" onClick={reset}>
          다시 시도
        </button>
      </section>
    </main>
  )
}
