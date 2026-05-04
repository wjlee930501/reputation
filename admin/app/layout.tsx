import type { Metadata } from 'next'
import Link from 'next/link'
import './globals.css'

export const metadata: Metadata = {
  title: 'Re:putation Ops',
  description: 'MotionLabs 내부 운영 콘솔 — 병원 AI 노출 운영',
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="ko">
      <body className="bg-slate-50 text-slate-900 min-h-screen antialiased">
        <div className="flex h-screen">
          {/* Sidebar */}
          <aside className="w-60 bg-slate-900 text-slate-100 flex flex-col flex-shrink-0">
            <div className="px-5 py-5 border-b border-slate-800">
              <Link href="/hospitals" className="block">
                <div className="flex items-baseline gap-1.5">
                  <span className="text-lg font-bold tracking-tight text-white">Re:putation</span>
                  <span className="text-[11px] font-semibold text-blue-300 uppercase tracking-wider">Ops</span>
                </div>
                <p className="text-[11px] text-slate-400 mt-0.5">MotionLabs 내부 운영 콘솔</p>
              </Link>
            </div>

            <nav className="flex-1 px-3 py-4 space-y-0.5">
              <p className="px-3 text-[10px] font-semibold uppercase tracking-wider text-slate-500 mb-2">
                Workspace
              </p>
              <Link
                href="/hospitals"
                className="flex items-center justify-between gap-2 px-3 py-2 rounded-md text-sm text-slate-200 hover:bg-slate-800 hover:text-white transition-colors group"
              >
                <span className="flex items-center gap-2.5">
                  <span aria-hidden>🏥</span>
                  병원 목록
                </span>
                <span className="text-[11px] text-slate-500 group-hover:text-slate-300">관리</span>
              </Link>
              <Link
                href="/hospitals/new"
                className="flex items-center gap-2.5 px-3 py-2 rounded-md text-sm text-slate-300 hover:bg-slate-800 hover:text-white transition-colors"
              >
                <span aria-hidden>＋</span>
                신규 병원 온보딩
              </Link>

              <p className="mt-6 px-3 text-[10px] font-semibold uppercase tracking-wider text-slate-500 mb-2">
                도움말
              </p>
              <div className="px-3 py-2 text-[11px] text-slate-400 leading-relaxed">
                병원 자료 → 콘텐츠 운영 기준(Essence) 승인 → 콘텐츠 자동 생성 → 발행 → 월간 리포트 순서로 진행합니다.
              </div>
            </nav>

            <div className="px-4 py-3 border-t border-slate-800 space-y-1">
              <p className="text-[11px] text-slate-300 font-medium">MotionLabs Inc.</p>
              <p className="text-[10px] text-slate-500">Internal Operator Console</p>
            </div>
          </aside>

          {/* Main Content */}
          <main className="flex-1 overflow-auto bg-slate-50">
            {children}
          </main>
        </div>
      </body>
    </html>
  )
}
