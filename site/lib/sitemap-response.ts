import type { MetadataRoute } from 'next'

import { buildSitemap, SitemapTransientError } from './sitemap-builder.ts'
import type { SitemapScope } from './sitemap-host.ts'

const XML_HEADERS = {
  'Content-Type': 'application/xml; charset=utf-8',
  // Host visibility is security-sensitive and upstream failures must reach crawlers as 503.
  // The process-local Next data cache is intentionally not used for this surface.
  'Cache-Control': 'no-store',
}

function xmlValue(value: string): string {
  return value
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&apos;')
}

function lastModifiedValue(value: string | Date | undefined): string | null {
  if (!value) return null
  const parsed = value instanceof Date ? value : new Date(value)
  return Number.isNaN(parsed.getTime()) ? null : parsed.toISOString()
}

export function renderSitemapXml(entries: MetadataRoute.Sitemap): string {
  const rows = entries.map((entry) => {
    const fields = [`<loc>${xmlValue(entry.url)}</loc>`]
    const lastModified = lastModifiedValue(entry.lastModified)
    if (lastModified) fields.push(`<lastmod>${lastModified}</lastmod>`)
    if (entry.changeFrequency) fields.push(`<changefreq>${entry.changeFrequency}</changefreq>`)
    if (typeof entry.priority === 'number') fields.push(`<priority>${entry.priority}</priority>`)
    return `  <url>${fields.join('')}</url>`
  })
  return [
    '<?xml version="1.0" encoding="UTF-8"?>',
    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ...rows,
    '</urlset>',
  ].join('\n')
}

export async function sitemapHttpResponse(
  scope: SitemapScope,
  apiBase: string | null,
): Promise<Response> {
  try {
    const entries = await buildSitemap(scope, apiBase)
    return new Response(renderSitemapXml(entries), { status: 200, headers: XML_HEADERS })
  } catch (error) {
    if (!(error instanceof SitemapTransientError)) throw error
    return new Response('Sitemap temporarily unavailable', {
      status: 503,
      headers: { ...XML_HEADERS, 'Retry-After': '60' },
    })
  }
}
