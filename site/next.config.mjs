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
const contentSecurityPolicy = [
  "default-src 'self'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
  "object-src 'none'",
  "script-src 'self' 'unsafe-inline'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob: https:",
  "font-src 'self' data:",
  "connect-src 'self'",
].join('; ')

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
  images: {
    remotePatterns: [
      { protocol: 'https', hostname: 'storage.googleapis.com' },
      { protocol: 'https', hostname: '*.storage.googleapis.com' },
      ...backendImageHosts,
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

export default nextConfig
