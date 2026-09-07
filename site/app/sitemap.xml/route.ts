import { headers } from 'next/headers'

import { getApiBase } from '@/lib/config'
import { resolveSitemapScope } from '@/lib/sitemap-host'
import { sitemapHttpResponse } from '@/lib/sitemap-response'

export async function GET(): Promise<Response> {
  const headerList = await headers()
  const scope = resolveSitemapScope(headerList.get('host'), headerList.get('x-forwarded-host'))
  return sitemapHttpResponse(scope, getApiBase(false))
}
