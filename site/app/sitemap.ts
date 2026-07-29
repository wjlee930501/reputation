import { MetadataRoute } from 'next'
import { headers } from 'next/headers'

import { getApiBase } from '@/lib/config'
import { buildSitemap } from '@/lib/sitemap-builder'
import { resolveSitemapScope } from '@/lib/sitemap-host'

export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  const apiBase = getApiBase(false)

  // 커스텀 도메인(또는 {slug}.{platform host} 하이브리드 서브도메인)에서 서빙되는
  // sitemap.xml에는 그 병원 하나의 URL만 실어야 한다 — robots.ts·middleware와 동일한
  // 호스트 판정(resolveRequestHost)으로 스코프를 정하고, 조립은 buildSitemap에 위임한다.
  const headerList = await headers()
  const scope = resolveSitemapScope(headerList.get('host'), headerList.get('x-forwarded-host'))

  return buildSitemap(scope, apiBase)
}
