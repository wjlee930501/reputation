import { MetadataRoute } from 'next'
import { platformSiteUrl } from '@/lib/site-url'

const DISALLOWED_PATHS = ['/api/', '/_next/', '/.well-known/']

// 이미지 프록시 경로만 크롤 허용 — 원장·병원 사진(/assets)과 콘텐츠 대표 이미지
// (/contents/*/image)는 backend 302 프록시라, /api/ 전체 차단 시 크롤러가 이미지를
// 못 가져와 ImageObject/OG 이미지가 AEO에 안 잡힌다. 나머지 /api/(JSON 등)는 계속 차단.
// allow가 더 구체적(longest-match)이라 /api/ disallow보다 우선한다.
const IMAGE_PROXY_ALLOW = [
  '/api/v1/public/hospitals/*/assets/',
  '/api/v1/public/hospitals/*/contents/*/image',
]
const ALLOWED_PATHS = ['/', ...IMAGE_PROXY_ALLOW]

export default function robots(): MetadataRoute.Robots {
  return {
    rules: [
      {
        userAgent: '*',
        allow: ALLOWED_PATHS,
        disallow: DISALLOWED_PATHS,
      },
      {
        userAgent: [
          'GPTBot',
          'OAI-SearchBot',
          'ChatGPT-User',
          'PerplexityBot',
          'ClaudeBot',
          'anthropic-ai',
          'Google-Extended',
          'Bingbot',
          'Googlebot',
        ],
        allow: ALLOWED_PATHS,
        disallow: DISALLOWED_PATHS,
      },
    ],
    sitemap: `${platformSiteUrl()}/sitemap.xml`,
  }
}
