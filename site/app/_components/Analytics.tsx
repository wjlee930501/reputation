'use client'

import Script from 'next/script'
import { usePathname } from 'next/navigation'
import { useEffect, useRef, useState } from 'react'

import { ensureAttributionCaptured } from '@/lib/ad-attribution-client'
import { configureGa, resolveGaPlan, trackPageView, type GaPlan } from '@/lib/analytics'
import {
  OPENAI_PIXEL_SDK_URL,
  initPixel,
  resolvePixelId,
  trackPixelPageViewed,
} from '@/lib/openai-pixel'

/**
 * GA4 + OpenAI 전환 픽셀 로더, 그리고 광고 유입 캡처 (REP-001·003, PIX-001·003).
 *
 * 서버가 아니라 클라이언트에서 판단하는 이유는 **한 배포가 여러 호스트를 서빙하기**
 * 때문이다. 플랫폼 도메인과 병원 커스텀 도메인은 같은 레이아웃을 쓰는데, GA4 쿠키
 * 도메인도 픽셀을 켤지 여부도 호스트마다 다르다. 빌드 타임에는 알 수 없는 값이다.
 *
 * 이 컴포넌트는 레이아웃에 **한 번만** 마운트된다. 픽셀 init을 여기서만 하는 것이
 * SPA 라우팅 중복 init을 막는 방법이다(PIX-001 주의사항 2).
 */
export default function Analytics() {
  const pathname = usePathname()
  const [gaPlan, setGaPlan] = useState<GaPlan | null>(null)
  const [pixelEnabled, setPixelEnabled] = useState(false)

  // 광고 파라미터는 태그보다 먼저 확보한다 — 스크립트 로드 실패와 무관하게
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
    setGaPlan(resolved)
  }, [])

  useEffect(() => {
    const pixelId = resolvePixelId(window.location.hostname, process.env.NEXT_PUBLIC_OPENAI_PIXEL_ID)
    if (!pixelId) return
    initPixel(pixelId)
    setPixelEnabled(true)
  }, [])

  return (
    <>
      {gaPlan ? (
        <Script
          src={`https://www.googletagmanager.com/gtag/js?id=${gaPlan.measurementId}`}
          strategy="afterInteractive"
        />
      ) : null}
      {pixelEnabled ? <Script src={OPENAI_PIXEL_SDK_URL} strategy="afterInteractive" /> : null}
      <RouteViews pathname={pathname} ga={Boolean(gaPlan)} pixel={pixelEnabled} />
    </>
  )
}

/**
 * 화면 전환 계측.
 *
 * GA4는 `gtag('config')`가 최초 1회 page_view를 이미 보냈으므로 **첫 경로는 건너뛴다** —
 * 그러지 않으면 랜딩 조회수가 두 배로 잡힌다. 반면 픽셀은 우리가 부르지 않으면 아무것도
 * 보내지 않으므로 첫 경로에서도 보낸다.
 */
function RouteViews({ pathname, ga, pixel }: { pathname: string; ga: boolean; pixel: boolean }) {
  const lastGaPath = useRef<string | null>(null)
  const lastPixelPath = useRef<string | null>(null)

  useEffect(() => {
    if (!ga) return
    if (lastGaPath.current === null) {
      lastGaPath.current = pathname
      return
    }
    if (lastGaPath.current === pathname) return
    lastGaPath.current = pathname
    trackPageView(pathname)
  }, [ga, pathname])

  useEffect(() => {
    if (!pixel) return
    if (lastPixelPath.current === pathname) return
    lastPixelPath.current = pathname
    trackPixelPageViewed(pathname)
  }, [pixel, pathname])

  return null
}
