import type { Metadata, Viewport } from 'next'
import { AdminShell } from './AdminShell'
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
      <body className="bg-slate-50 text-slate-900 min-h-screen antialiased">
        <a
          href="#main-content"
          className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-50 focus:rounded-md focus:bg-white focus:px-3 focus:py-2 focus:text-sm focus:font-semibold focus:text-slate-900 focus:shadow"
        >
          본문으로 이동
        </a>
        <AdminShell>{children}</AdminShell>
      </body>
    </html>
  )
}
