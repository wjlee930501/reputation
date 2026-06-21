import { MetadataRoute } from 'next'
import { headers } from 'next/headers'

import { resolveSitemapUrl } from '@/lib/robots-host'

const DISALLOWED_PATHS = ['/api/', '/_next/', '/.well-known/']

export default async function robots(): Promise<MetadataRoute.Robots> {
  // 커스텀 도메인에서 robots.txt가 응답될 때 sitemap 포인터를 요청 origin으로 맞춘다.
  const headerList = await headers()
  const sitemap = resolveSitemapUrl(headerList.get('host'), headerList.get('x-forwarded-proto'))
  return {
    rules: [
      {
        userAgent: '*',
        allow: '/',
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
        allow: '/',
        disallow: DISALLOWED_PATHS,
      },
    ],
    sitemap,
  }
}
