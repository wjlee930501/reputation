/** Cache identity is the same slug on fetch and trusted invalidation. */
export function hospitalCacheTag(slug: string): string | null {
  return /^[^\s/\\?#\u0000-\u001f]{1,200}$/.test(slug) ? `hospital:${slug}` : null
}

type NextOptions = { revalidate?: number | false; tags?: string[] }
type TaggedInit = RequestInit & { next?: NextOptions }

export function withHospitalCache(slug: string, init: RequestInit): RequestInit {
  if (init.cache === 'no-store') return init
  const tag = hospitalCacheTag(slug)
  if (!tag) return { ...init, cache: 'no-store' }
  const next = (init as TaggedInit).next ?? {}
  return { ...init, next: { ...next, tags: [...new Set([...(next.tags ?? []), tag])] } } as TaggedInit
}

const PLATFORM_PATHS = new Set(['sitemap.xml', 'llms.txt', 'robots.txt', 'api', 'ai-diagnosis', 'privacy', 'terms'])
export function hospitalTagsForPaths(paths: string[]): string[] {
  const tags = new Set<string>()
  for (const path of paths) {
    if (!path.startsWith('/') || path.startsWith('//')) continue
    const slug = path.split('/')[1]
    if (!slug || PLATFORM_PATHS.has(slug)) continue
    const tag = hospitalCacheTag(slug)
    if (tag) tags.add(tag)
  }
  return [...tags]
}
