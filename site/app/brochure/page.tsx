import type { Metadata } from 'next'
import { cookies } from 'next/headers'

import { BROCHURE_COOKIE, verifyBrochureToken } from '@/lib/brochure'

import { SiteFooter, SiteHeader } from '../_components/SiteChrome'
import BrochureExperience from './_components/BrochureExperience'
import BrochureNextStep from './_components/BrochureNextStep'

export const metadata: Metadata = {
  title: '서비스 소개서 | Re:putation',
  description: 'AI 답변 속 병원 추천 구조부터 운영 방식, 측정 방법, 요금까지 12페이지로 정리한 Re:putation 서비스 소개서입니다.',
  // 게이트 뒤의 자료라 검색 결과에 걸릴 이유가 없다. 홈이 이 역할을 한다.
  robots: { index: false, follow: true },
}

// 열람 쿠키를 읽어 게이트를 건너뛸지 정한다.
export const dynamic = 'force-dynamic'

export default async function BrochurePage() {
  const store = await cookies()
  const token = verifyBrochureToken(store.get(BROCHURE_COOKIE)?.value)

  return (
    <main id="main-content" className="landing-shell landing-edge landing-sub">
      <SiteHeader />
      <BrochureExperience initialToken={token} />
      <BrochureNextStep />
      <SiteFooter />
    </main>
  )
}
