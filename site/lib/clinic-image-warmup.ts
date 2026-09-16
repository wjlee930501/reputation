import { fetchHospital } from './api.ts'
import { selectClinicHeroImage } from './clinic-theme.ts'
import { CLINIC_IMAGE_QUALITY } from './clinic-image-delivery.ts'
import { isOffAllowlistExternalUrl } from './image-policy.ts'

export const IMAGE_WARMUP_BUDGET_MS = 2500
const lastAttempts = new Map<string, number>()
const RESERVED = new Set(['api', 'sitemap.xml', 'robots.txt', 'llms.txt', 'privacy', 'terms', 'ai-diagnosis'])

export function imageWarmupSlug(paths: string[]): string | null {
  return paths.map(path => /^\/([a-z0-9][a-z0-9-]{0,62})$/.exec(path)?.[1])
    .find((slug): slug is string => Boolean(slug && !RESERVED.has(slug))) ?? null
}

export function imageWarmupOrigin(env: { K_SERVICE?: string; PORT?: string }): string | null {
  if (env.K_SERVICE !== 'reputation-site' || !/^\d{1,5}$/.test(env.PORT ?? '')) return null
  const port = Number(env.PORT)
  return port > 0 && port <= 65535 ? `http://127.0.0.1:${port}` : null
}

export function imageWarmupUrls(origin: string, source: string): string[] {
  return [750, 1200].map(width => {
    const url = new URL('/_next/image', origin)
    url.search = new URLSearchParams({ url: source, w: String(width), q: String(CLINIC_IMAGE_QUALITY) }).toString()
    return url.href
  })
}

type Dependencies = { fetch: typeof fetch; hero: (slug: string, signal: AbortSignal) => Promise<string | null> }

/** Called only AFTER authenticated cache invalidation. Public authorization is
 * rechecked by fetchHospital and the existing asset endpoint. At most one clinic,
 * two sizes and 2.5 seconds; failure cannot undo or block withdrawal/invalidation.
 * Self-requests use a fixed loopback origin, never request Host/forwarded headers.
 */
export async function warmClinicHeroImages(paths: string[],
  env = { K_SERVICE: process.env.K_SERVICE, PORT: process.env.PORT },
  dependencies: Dependencies = {
    fetch,
    hero: async (slug, signal) => selectClinicHeroImage(await fetchHospital(slug, { cache: 'no-store', signal })),
  },
): Promise<number> {
  const origin = imageWarmupOrigin(env), slug = imageWarmupSlug(paths)
  if (!origin || !slug) return 0
  const signal = AbortSignal.timeout(IMAGE_WARMUP_BUDGET_MS)
  try {
    const source = await dependencies.hero(slug, signal)
    if (!source?.startsWith('https://') || isOffAllowlistExternalUrl(source)) return 0
    if (Date.now() - (lastAttempts.get(source) ?? 0) < 60000) return 0
    if (lastAttempts.size >= 128) lastAttempts.delete(lastAttempts.keys().next().value!)
    lastAttempts.set(source, Date.now())
    const results = await Promise.allSettled(imageWarmupUrls(origin, source).map(async url => {
      const response = await dependencies.fetch(url, { signal, headers: { Accept: 'image/avif,image/webp,image/*' } })
      if (!response.ok || !response.headers.get('content-type')?.startsWith('image/')) { await response.body?.cancel(); return false }
      await response.arrayBuffer()
      return true
    }))
    return results.filter(result => result.status === 'fulfilled' && result.value).length
  } catch {
    return 0 // Optimizer/network failure never reverses successful cache invalidation.
  }
}
