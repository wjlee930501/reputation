import { MetadataRoute } from 'next'
import { headers } from 'next/headers'

import { AI_SEARCH_USER_AGENTS } from '@/lib/ai-crawlers'
import { resolveSitemapUrl } from '@/lib/robots-host'
import { ROBOTS_ALLOWED_PATHS, ROBOTS_DISALLOWED_PATHS } from '@/lib/robots-policy'

export default async function robots(): Promise<MetadataRoute.Robots> {
  // 커스텀 도메인에서 robots.txt가 응답될 때 sitemap 포인터를 요청 origin으로 맞춘다.
  const headerList = await headers()
  const sitemap = resolveSitemapUrl(
    headerList.get('host'),
    headerList.get('x-forwarded-proto'),
    headerList.get('x-forwarded-host'),
  )

  return {
    rules: [
      {
        userAgent: '*',
        allow: ROBOTS_ALLOWED_PATHS,
        disallow: ROBOTS_DISALLOWED_PATHS,
      },
      {
        userAgent: [...AI_SEARCH_USER_AGENTS],
        allow: ROBOTS_ALLOWED_PATHS,
        disallow: ROBOTS_DISALLOWED_PATHS,
      },
    ],
    sitemap,
  }
}
