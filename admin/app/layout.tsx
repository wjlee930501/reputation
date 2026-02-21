import type { Metadata } from 'next'
import Link from 'next/link'
import './globals.css'

export const metadata: Metadata = {
  title: 'Re:putation Admin',
  description: 'MotionLabs AEO 관리 시스템',
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="ko">
      <body className="bg-gray-50 min-h-screen">
        <div className="flex h-screen">
          {/* Sidebar */}
          <aside className="w-56 bg-gray-900 text-white flex flex-col flex-shrink-0">
            <div className="px-6 py-5 border-b border-gray-700">
              <h1 className="text-lg font-bold tracking-tight">Re:putation</h1>
              <p className="text-xs text-gray-400 mt-0.5">Admin Panel</p>
            </div>
            <nav className="flex-1 px-3 py-4 space-y-1">
              <Link
                href="/hospitals"
                className="flex items-center gap-2 px-3 py-2 rounded-md text-sm text-gray-300 hover:bg-gray-800 hover:text-white transition-colors"
              >
                <span>🏥</span>
                병원 목록
              </Link>
            </nav>
            <div className="px-4 py-3 border-t border-gray-700">
              <p className="text-xs text-gray-500">MotionLabs Inc.</p>
            </div>
          </aside>

          {/* Main Content */}
          <main className="flex-1 overflow-auto">
            {children}
          </main>
        </div>
      </body>
    </html>
  )
}
