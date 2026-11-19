import type { Metadata } from 'next'
import '../survey.css'
import SurveyApp from './SurveyApp'

// 병원마다 발급된 개인 링크다 — 색인·캐시 대상이 아니다.
export const metadata: Metadata = {
  title: '사전 설문 | Re:putation',
  robots: { index: false, follow: false, nocache: true },
  referrer: 'no-referrer',
}

export const dynamic = 'force-dynamic'

export default async function DoctorSurveyPage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = await params
  return <SurveyApp token={token} />
}
