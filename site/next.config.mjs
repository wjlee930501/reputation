import path from 'node:path'
import { fileURLToPath } from 'node:url'

const appDir = path.dirname(fileURLToPath(import.meta.url))

function remotePatternFromEnv(value) {
  if (!value) return null
  try {
    const parsed = new URL(value)
    return {
      protocol: parsed.protocol.replace(':', ''),
      hostname: parsed.hostname,
      port: parsed.port,
    }
  } catch {
    return null
  }
}

const backendImageHosts = [
  'http://localhost:8000',
  'http://127.0.0.1:8000',
  process.env.NEXT_PUBLIC_BACKEND_URL,
  process.env.NEXT_PUBLIC_API_URL,
  process.env.BACKEND_URL,
]
  .map(remotePatternFromEnv)
  .filter(Boolean)

// CSP — 사이트가 실제로 로드하는 출처만 허용한다.
// - script-src: Next.js 인라인 부트스트랩 + JSON-LD(JsonLd.tsx의 dangerouslySetInnerHTML) → 'unsafe-inline'
// - style-src: next/image·Tailwind·인라인 style 속성 → 'unsafe-inline'
// - img-src: GCS(이미지), 백엔드 자산, AE가 입력한 외부 원장 사진(https) + next/image data/blob
// - font-src: 자체 호스팅 Pretendard woff2
// - connect-src: 백엔드 호출은 모두 서버(SSG/ISR)에서 일어나므로 브라우저는 same-origin만 사용
const isDev = process.env.NODE_ENV !== 'production'

// dev 전용: 로컬 backend(http://localhost:8000)에서 서빙되는 원장·콘텐츠 자산 이미지를
// 미리보기·검증할 수 있게 img-src에 로컬 오리진을 더한다. 프로덕션(https/GCS)은 영향 없음.
const devLocalImgSrc = isDev ? ' http://localhost:8000 http://127.0.0.1:8000' : ''

// dev 전용: Next.js dev 서버(HMR/webpack)는 eval을 사용하므로 script-src에 'unsafe-eval'이
// 없으면 클라이언트 하이드레이션이 실패한다(모든 client component 무동작). 프로덕션 빌드는
// eval을 쓰지 않으므로 이 완화는 프로덕션에 영향이 없다.
const devUnsafeEval = isDev ? " 'unsafe-eval'" : ''

// GA4(gtag.js) 출처 — Google이 공개한 필요 출처 그대로다.
// 이걸 빠뜨리면 계측 코드가 있어도 **스크립트 로드 자체가 CSP에 막혀** 이벤트가 한 건도
// 나가지 않는다. 화면에는 아무 증상이 없고 콘솔에만 남으므로 놓치기 쉽다.
const GA_SCRIPT_SRC = 'https://*.googletagmanager.com'
const GA_CONNECT_SRC =
  'https://*.google-analytics.com https://*.analytics.google.com https://*.googletagmanager.com'

// OpenAI 전환 픽셀(oaiq) 출처 — SDK는 CDN에서 받고, 이벤트는 수집 엔드포인트로 보낸다.
// 픽셀별 설정 조회가 CDN으로도 나가므로 connect-src에 둘 다 필요하다.
const OPENAI_PIXEL_SCRIPT_SRC = 'https://bzrcdn.openai.com'
const OPENAI_PIXEL_CONNECT_SRC = 'https://bzr.openai.com https://bzrcdn.openai.com'

const contentSecurityPolicy = [
  "default-src 'self'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
  "object-src 'none'",
  `script-src 'self' 'unsafe-inline' ${GA_SCRIPT_SRC} ${OPENAI_PIXEL_SCRIPT_SRC}${devUnsafeEval}`,
  "style-src 'self' 'unsafe-inline'",
  // img-src는 이미 https: 전체를 허용하므로 GA·픽셀의 이미지 폴백은 따로 적지 않는다.
  `img-src 'self' data: blob: https:${devLocalImgSrc}`,
  "font-src 'self' data:",
  `connect-src 'self' ${GA_CONNECT_SRC} ${OPENAI_PIXEL_CONNECT_SRC}`,
].join('; ')

// 소개서 문서(`/brochure/doc`)만 같은 출처의 iframe 임베드를 허용한다. 나머지 경로는
// 그대로 `frame-ancestors 'none'` + `X-Frame-Options: DENY`다. Next는 같은 키가 여러 규칙에
// 걸리면 **뒤에 선언한 값**을 쓰므로, 아래 headers()에서 이 규칙을 전역 규칙 뒤에 둔다.
const brochureFrameHeaders = [
  { key: 'Content-Security-Policy', value: contentSecurityPolicy.replace("frame-ancestors 'none'", "frame-ancestors 'self'") },
  { key: 'X-Frame-Options', value: 'SAMEORIGIN' },
]

const securityHeaders = [
  { key: 'Content-Security-Policy', value: contentSecurityPolicy },
  { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
  { key: 'X-Content-Type-Options', value: 'nosniff' },
  { key: 'X-Frame-Options', value: 'DENY' },
  { key: 'Permissions-Policy', value: 'camera=(), microphone=(), geolocation=()' },
]

if (process.env.NODE_ENV === 'production') {
  securityHeaders.push({
    key: 'Strict-Transport-Security',
    value: 'max-age=63072000; includeSubDomains; preload',
  })
}

/** @type {import('next').NextConfig} */
const nextConfig = {
  // Cloud Run 컨테이너 배포용 — .next/standalone에 self-contained 서버 번들 생성.
  output: 'standalone',
  outputFileTracingRoot: appDir,
  // 소개서 원본은 라우트가 런타임에 파일로 읽는다 — standalone 번들에 함께 실어야 한다.
  outputFileTracingIncludes: {
    '/brochure/doc': ['./content/brochure/**/*'],
    // 병원 모노그램 탭 아이콘이 런타임에 읽는 폰트.
    '/favicon/[slug]': ['./assets/fonts/Pretendard-Bold.subset.otf'],
  },
  images: {
    // Keep negotiated AVIF/WebP: WebP-only increased mobile transfer in measured photos.
    formats: ['image/avif', 'image/webp'],
    imageSizes: [32, 48, 64, 96, 128, 192, 256, 320, 384],
    deviceSizes: [640, 750, 828, 1080, 1200, 1600, 1920, 2048, 3840],
    qualities: [75, 84],
    minimumCacheTTL: 86400,
    remotePatterns: [
      { protocol: 'https', hostname: 'storage.googleapis.com' },
      { protocol: 'https', hostname: '*.storage.googleapis.com' },
      { protocol: 'https', hostname: 'reputation.motionlabs.kr' },
      ...backendImageHosts,
    ],
  },
  // 무료 진단 셀프 신청 화면은 닫았다. 진단 리포트는 도입문의 뒤 담당 마케터가 만들어
  // 연락과 함께 전달한다. 광고·북마크로 들어오는 방문은 도입문의로 보낸다(쿼리는 유지된다).
  // 기존 신청자의 결과 확인 경로(/ai-diagnosis/status/…)는 이 규칙에 걸리지 않는다.
  async redirects() {
    return [{ source: '/ai-diagnosis', destination: '/contact', permanent: false }]
  },
  async headers() {
    return [
      {
        source: '/:path*',
        headers: securityHeaders,
      },
      {
        source: '/brochure/doc',
        headers: brochureFrameHeaders,
      },
    ]
  },
}

export default nextConfig
