import type { Metadata } from 'next'
import Link from 'next/link'

export const metadata: Metadata = {
  title: '페이지를 찾을 수 없습니다',
  robots: { index: false, follow: false },
}

// 브랜드된 404 — notFound() 호출 시 Next 기본 화면 대신 노출.
//
// **Re:putation 헤더·푸터·도입문의 CTA를 붙이지 않는다.** 이 화면은 병원 커스텀
// 도메인에서 없는 경로를 열었을 때도 뜬다(proxy가 /{slug}/…로 rewrite한 뒤 라우트가
// 없으면 루트 404로 떨어진다). 환자가 보는 자리에 우리 영업 문구가 나오면 안 된다.
// 타이포와 면만 랜딩과 맞추고, 버튼은 오렌지가 아니라 먹색으로 둔다.
export default function NotFound() {
  return (
    <main id="main-content" className="landing-shell landing-edge landing-sub">
      <section className="edge-message">
        <p className="edge-message-code">404</p>
        <h1>페이지를 찾을 수 없습니다</h1>
        <p>주소가 잘못되었거나 페이지가 이동·삭제되었을 수 있습니다.</p>
        <Link className="btn edge-message-action" href="/">
          홈으로 이동
        </Link>
      </section>
    </main>
  )
}
