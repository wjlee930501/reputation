import type { Metadata, Viewport } from 'next'
import { AdminShell } from './AdminShell'
import './globals.css'

export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
}

export const metadata: Metadata = {
  title: 'Re:putation Ops · MotionLabs Research Preview',
  description: 'MotionLabs Research Preview · 병원 AI 노출 운영 내부 콘솔',
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="ko">
      <body className="bg-slate-50 text-slate-900 min-h-screen antialiased">
        <AdminShell>{children}</AdminShell>
      </body>
    </html>
  )
}
