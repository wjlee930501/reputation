import { headers } from 'next/headers'

import {
  getPrimaryHostnames,
  isPrimaryHost,
  normalizeHostname,
  resolveRequestHost,
} from '@/lib/host-routing'
import { fetchDaumVerificationForHost } from '@/lib/ownership-verification'
import { resolveSitemapUrl } from '@/lib/robots-host'
import { renderRobotsText } from '@/lib/robots-text'
import { platformSiteUrl } from '@/lib/site-url'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'

export async function GET(): Promise<Response> {
  const headerList = await headers()
  const host = headerList.get('host')
  const forwardedHost = headerList.get('x-forwarded-host')
  const forwardedProto = headerList.get('x-forwarded-proto')
  const primaryHostnames = getPrimaryHostnames(platformSiteUrl())
  const requestHost = resolveRequestHost(host, forwardedHost, primaryHostnames)
  const customHostname = isPrimaryHost(requestHost, primaryHostnames)
    ? null
    : normalizeHostname(requestHost)
  const daumVerification = customHostname
    ? await fetchDaumVerificationForHost(customHostname)
    : null
  const sitemap = resolveSitemapUrl(host, forwardedProto, forwardedHost)

  return new Response(renderRobotsText(sitemap, daumVerification), {
    headers: {
      'content-type': 'text/plain; charset=utf-8',
      'cache-control': 'public, max-age=0, must-revalidate',
    },
  })
}
