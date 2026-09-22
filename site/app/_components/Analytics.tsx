'use client'

import Script from 'next/script'
import { usePathname } from 'next/navigation'
import { useEffect, useRef, useState } from 'react'

import { ensureAttributionCaptured } from '@/lib/ad-attribution-client'
import { configureGa, resolveGaPlan, trackPageView, type GaPlan } from '@/lib/analytics'

/**
 * GA4 로더 + 광고 유입 캡처 (작업지시서 REP-001·REP-003).
 *
 * 서버가 아니라 클라이언트에서 판단하는 이유는 **한 배포가 여러 호스트를 서빙하기**
 * 때문이다. 플랫폼 도메인과 병원 커스텀 도메인은 같은 레이아웃을 쓰는데, 쿠키 도메인은
 * 호스트마다 달라야 한다(`resolveGaPlan`). 빌드 타임에는 알 수 없는 값이다.
 */
export default function Analytics() {
  const pathname = usePathname()
  const [plan, setPlan] = useState<GaPlan | null>(null)

  // 광고 파라미터는 GA4보다 먼저 확보한다 — 스크립트 로드 실패와 무관하게
  // 리드 레코드에는 유입 경로가 남아야 한다.
  useEffect(() => {
    ensureAttributionCaptured()
  }, [])

  useEffect(() => {
    const resolved = resolveGaPlan(window.location.hostname, process.env.NEXT_PUBLIC_GA_MEASUREMENT_ID)
    if (!resolved) return
    // 원격 스크립트를 기다리지 않는다. 스텁이 호출을 dataLayer에 쌓아 두므로 순서는
    // 보장되고, 폼이 먼저 쌓아 둔 이벤트도 여기서 config 뒤에 이어 붙는다.
    configureGa(resolved)
    setPlan(resolved)
  }, [])

  return (
    <>
      {plan ? (
        <>
          <Script
            src={`https://www.googletagmanager.com/gtag/js?id=${plan.measurementId}`}
            strategy="afterInteractive"
          />
          <RouteChangePageViews pathname={pathname} enabled />
        </>
      ) : null}
    </>
  )
}

/**
 * 클라이언트 이동의 page_view. 최초 로드는 `gtag('config')`가 이미 보냈으므로 건너뛴다 —
 * 그러지 않으면 랜딩 조회수가 두 배로 잡힌다.
 */
function RouteChangePageViews({ pathname, enabled }: { pathname: string; enabled: boolean }) {
  const lastPath = useRef<string | null>(null)

  useEffect(() => {
    if (!enabled) return
    if (lastPath.current === null) {
      lastPath.current = pathname
      return
    }
    if (lastPath.current === pathname) return
    lastPath.current = pathname
    trackPageView(pathname)
  }, [enabled, pathname])

  return null
}
