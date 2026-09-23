import type { Metadata } from 'next'
import { cookies } from 'next/headers'
import { redirect } from 'next/navigation'

import { BROCHURE_COOKIE, verifyBrochureToken } from '@/lib/brochure'

import { SiteFooter, SiteHeader } from '../../../_components/SiteChrome'
import BrochureNextStep from '../../_components/BrochureNextStep'
import BrochureViewer from '../../_components/BrochureViewer'

export const metadata: Metadata = {
  title: '서비스 소개서 | Re:putation',
  robots: { index: false, follow: false },
}

export const dynamic = 'force-dynamic'

/**
 * 원장이 복사해 건넨 공유 링크. 받은 사람(실장·공동원장)은 다시 입력하지 않고 바로 본다.
 * 열람 기록은 원래 원장의 토큰에 "shared"로 붙어, 병원 안에서 검토가 시작됐다는 신호가 된다.
 * 링크를 연 사람이 원장 본인(열람 쿠키 보유)이면 본인 열람으로 센다.
 */
export default async function SharedBrochurePage({ params }: { params: Promise<{ token: string }> }) {
  const { token: raw } = await params
  const token = verifyBrochureToken(decodeURIComponent(raw))
  if (!token) redirect('/brochure')

  const store = await cookies()
  const own = verifyBrochureToken(store.get(BROCHURE_COOKIE)?.value)
  const viewer = own ? 'owner' : 'shared'

  return (
    <main id="main-content" className="landing-shell landing-edge landing-sub">
      <SiteHeader />
      <section className="brochure-section brochure-section--view" aria-labelledby="brochure-heading">
        <div className="brochure-inner">
          <div className="brochure-view-head">
            <p className="section-label">공유받은 소개서</p>
            <h1 id="brochure-heading">Re:putation 서비스 소개서</h1>
          </div>
          <BrochureViewer token={own ?? token} viewer={viewer} />
        </div>
      </section>
      <BrochureNextStep />
      <SiteFooter />
    </main>
  )
}
