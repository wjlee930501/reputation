import type { Metadata } from 'next'

import { fetchContents, fetchSitePreview, HospitalNotFoundError, type ContentSummary } from '@/lib/api'

import { HospitalHome } from '../../[slug]/_layouts/HospitalHome'

/*
 * 섹션 설정(레이아웃 편집기) 초안 미리보기 — 설계 5절·13절.
 *
 * 주소는 플랫폼 호스트의 `/preview/{token}`이다. 토큰이 병원·초안·revision을 갖고 있어 slug가 없고,
 * 공개 전 병원도 연다(병원 주소는 공개 병원만 열린다). 홈과 같은 `HospitalHome`으로 그린다 —
 * 미리보기 전용 화면을 따로 두지 않아 공개 반영 뒤 화면과 갈라지지 않는다.
 *
 * 캐시·색인·분석: 이 경로는 매번 새로 그리고(`force-dynamic`, 조회 `no-store`), next.config가
 * `Cache-Control: no-store`·`X-Robots-Tag: noindex`를 붙이며, 분석 태그는 싣지 않는다(`isPrivateLinkPath`).
 * 어드민 편집 화면의 iframe에만 열린다(`frame-ancestors`).
 */
export const dynamic = 'force-dynamic'

export const metadata: Metadata = {
  title: '병원 화면 미리보기 | Re:putation',
  robots: { index: false, follow: false, nocache: true },
  referrer: 'no-referrer',
}

export default async function SitePreviewPage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = await params
  const preview = await fetchSitePreview(token)
  if (!preview) return <PreviewUnavailable />
  const { hospital } = preview
  // 글 목록은 공개 병원만 열린다 — 공개 전 병원은 글 없이 그린다(실제 공개 첫 화면과 같다).
  let contents: ContentSummary[] = []
  try {
    contents = await fetchContents(hospital.slug, 60, { cache: 'no-store' })
  } catch (error) {
    if (!(error instanceof HospitalNotFoundError)) throw error
  }
  return (
    <>
      <p role="status" style={BANNER_STYLE}>
        미리보기 — 아직 공개 반영되지 않은 화면입니다. 링크는 10분 동안만 열립니다.
      </p>
      <HospitalHome hospital={hospital} contents={contents} slug={hospital.slug} />
    </>
  )
}

const BANNER_STYLE = {
  position: 'sticky',
  top: 0,
  zIndex: 1000,
  margin: 0,
  padding: '8px 16px',
  background: '#1f2937',
  color: '#ffffff',
  fontSize: 14,
  textAlign: 'center',
} as const

function PreviewUnavailable() {
  return (
    <main style={{ maxWidth: 560, margin: '96px auto', padding: '0 16px', textAlign: 'center' }}>
      <h1 style={{ fontSize: 22, marginBottom: 12 }}>미리보기를 열 수 없습니다</h1>
      <p>링크가 만료됐거나, 그 뒤에 설정을 저장·공개 반영해 새 미리보기가 필요합니다. 편집 화면에서 미리보기를 다시 열어 주세요.</p>
    </main>
  )
}
