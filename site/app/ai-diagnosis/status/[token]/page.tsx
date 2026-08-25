import type { Metadata } from 'next'
import StatusView from './StatusView'

// 개인에게 발급된 링크다 — 색인 대상이 아니다 (PRD F5-4).
export const metadata: Metadata = {
  title: 'AI 노출 진단 진행 상황 | Re:putation',
  robots: { index: false, follow: false, nocache: true },
}

export const dynamic = 'force-dynamic'

export default async function DiagnosisStatusPage({
  params,
}: {
  params: Promise<{ token: string }>
}) {
  const { token } = await params
  return (
    <main id="main-content" className="dg-page">
      <StatusView token={token} />
    </main>
  )
}
