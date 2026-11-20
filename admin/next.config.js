const path = require('path')

// 레이아웃 편집기 미리보기는 사이트의 /preview/{token}을 같은 화면 iframe에 띄운다. 사이트 주소는
// lib/platform-domain.ts와 같은 규칙(NEXT_PUBLIC_SITE_URL, 없으면 운영 기본 주소)으로 정한다 — Admin
// 이미지는 이 값을 빌드 인자로 받지 않으므로 운영에서는 기본 주소가 쓰인다.
const DEFAULT_SITE_ORIGIN = 'https://reputation.motionlabs.kr'

function siteOrigin() {
  const raw = (process.env.NEXT_PUBLIC_SITE_URL || '').trim()
  if (!raw) return DEFAULT_SITE_ORIGIN
  try {
    return new URL(raw).origin
  } catch {
    return DEFAULT_SITE_ORIGIN
  }
}

const contentSecurityPolicy = [
  "default-src 'self'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
  "object-src 'none'",
  "script-src 'self' 'unsafe-inline'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob: https://storage.googleapis.com https://*.storage.googleapis.com",
  "font-src 'self' data:",
  "connect-src 'self'",
  // 보고서 고치기의 PDF 미리보기는 같은 화면 안 iframe에 blob: URL로 띄운다(새 탭은 팝업 차단을 받는다).
  `frame-src 'self' blob: ${siteOrigin()}`,
].join('; ')

const securityHeaders = [
  { key: 'Content-Security-Policy', value: contentSecurityPolicy },
  { key: 'Referrer-Policy', value: 'no-referrer' },
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
  outputFileTracingRoot: path.resolve(__dirname),
  turbopack: {
    root: path.resolve(__dirname),
  },
  images: {
    remotePatterns: [
      { protocol: 'https', hostname: 'storage.googleapis.com' },
      { protocol: 'https', hostname: '*.storage.googleapis.com' },
    ],
  },
  async headers() {
    return [
      {
        source: '/:path*',
        headers: securityHeaders,
      },
    ]
  },
}

module.exports = nextConfig
