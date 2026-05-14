import type { Metadata, Viewport } from 'next'
import Link from 'next/link'
import './globals.css'

export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
}

export const metadata: Metadata = {
  title: 'Re:putation Ops · MotionLabs',
  description: 'MotionLabs 병원 AI 노출 운영 내부 콘솔',
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="ko">
      <body className="admin-shell min-h-screen antialiased">
        <div className="flex h-screen">
          {/* Sidebar */}
          <aside className="admin-sidebar flex w-60 flex-shrink-0 flex-col">
            <div className="border-b border-white/10 px-5 py-5">
              <Link href="/hospitals" className="block">
                <div className="flex items-baseline gap-1.5">
                  <span className="title1 text-white">Re:putation</span>
                  <span className="details2 text-[var(--color-revisit-primary-80)]">운영</span>
                </div>
                <p className="details2 mt-0.5 text-[var(--color-revisit-coolgrey-70)]">
                  MotionLabs 내부 운영 콘솔
                </p>
                <span className="details3 mt-2 inline-flex items-center gap-1 rounded-full border border-white/15 px-2 py-0.5 text-[var(--color-revisit-primary-80)]">
                  <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-[var(--color-revisit-primary-80)]" />
                  Operations
                </span>
              </Link>
            </div>

            <nav className="flex-1 space-y-0.5 px-3 py-4">
              <p className="details3 mb-2 px-3 uppercase text-[var(--color-revisit-coolgrey-60)]">
                운영 메뉴
              </p>
              <Link
                href="/hospitals"
                className="admin-sidebar-link justify-between group"
              >
                <span className="flex items-center gap-2.5">
                  <span className="admin-nav-glyph" aria-hidden>H</span>
                  병원 목록
                </span>
                <span className="details3 text-[var(--color-revisit-coolgrey-60)] group-hover:text-[var(--color-revisit-coolgrey-80)]">
                  관리
                </span>
              </Link>
              <Link
                href="/hospitals/new"
                className="admin-sidebar-link"
              >
                <span className="admin-nav-glyph" aria-hidden>+</span>
                신규 병원 온보딩
              </Link>
              <Link
                href="/leads"
                className="admin-sidebar-link"
              >
                <span className="admin-nav-glyph" aria-hidden>L</span>
                상담 리드
              </Link>

              <p className="details3 mb-2 mt-6 px-3 uppercase text-[var(--color-revisit-coolgrey-60)]">
                도움말
              </p>
              <div className="details2 px-3 py-2 text-[var(--color-revisit-coolgrey-70)]">
                병원 자료 → 콘텐츠 운영 기준 승인 → 콘텐츠 가이드 작성 → 발행 검수 → 월간 리포트 순서로 진행합니다.
              </div>
            </nav>

            <div className="space-y-1 border-t border-white/10 px-4 py-3">
              <p className="details2 text-[var(--color-revisit-coolgrey-80)]">MotionLabs Inc.</p>
              <p className="details3 text-[var(--color-revisit-coolgrey-60)]">v1.0 · 내부 운영 콘솔</p>
              <a
                href="https://motionlabs.kr"
                target="_blank"
                rel="noopener"
                className="details3 text-[var(--color-revisit-primary-80)] hover:text-white"
              >
                motionlabs.kr ↗
              </a>
            </div>
          </aside>

          {/* Main Content */}
          <main className="admin-surface flex-1 overflow-auto">
            {children}
          </main>
        </div>
      </body>
    </html>
  )
}
