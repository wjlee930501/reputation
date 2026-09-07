// sitemap.ts의 엔트리 조립 로직 — 요청 컨텍스트(next/headers)에 의존하지 않는 순수/네트워크
// 계층으로 분리해 단위 테스트가 가능하게 한다(sitemap.ts는 headers()→scope만 넘기는 얇은 래퍼).
//
// 스코프별 계약:
//   - 'all'  : 플랫폼 루트 sitemap. 플랫폼 루트 + /llms.txt만 포함한다. 병원 URL의
//              canonical origin은 각 `{slug}.{platform host}` 또는 자기 도메인이므로
//              각 tenant host의 sitemap이 소유한다.
//   - 'host' : 커스텀 도메인(또는 {slug}.{platform host} 하이브리드 서브도메인) sitemap.
//              그 병원 하나의 URL만 싣는다 — 플랫폼 엔트리는 절대 넣지 않는다(넣으면
//              커스텀 도메인 sitemap에 플랫폼/타 병원 URL이 함께 노출된다).

import type { MetadataRoute } from 'next'

import type { SitemapScope } from './sitemap-host.ts'
import { platformSiteUrl } from './site-url.ts'
import { buildTreatmentSlug } from './treatment-slug.ts'

// 백엔드 /contents 목록의 하드캡과 동일 — offset으로 페이지를 넘겨 전체 발행 콘텐츠를 순회한다.
const CONTENT_PAGE_SIZE = 500

/** Upstream failure that must not be turned into a complete, cacheable sitemap. */
export class SitemapTransientError extends Error {
  constructor(message: string, options?: ErrorOptions) {
    super(message, options)
    this.name = 'SitemapTransientError'
  }
}

/** The tenant or its public content disappeared while the sitemap was being assembled. */
class SitemapPrivateVisibilityError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'SitemapPrivateVisibilityError'
  }
}

export interface HospitalEntry {
  slug: string
  aeo_domain?: string | null
  updated_at?: string
  treatments?: Array<{ name: string }>
}

interface ContentEntry {
  id: string | number
  published_at: string | null
  body_updated_at?: string | null
  scheduled_date: string
}

// 플랫폼(전체 병원) sitemap에만 싣는 루트 + /llms.txt 엔트리.
export function platformBaseEntries(): MetadataRoute.Sitemap {
  const siteUrl = platformSiteUrl()
  return [
    {
      url: siteUrl,
      changeFrequency: 'daily',
      priority: 1.0,
    },
    {
      url: `${siteUrl}/llms.txt`,
      changeFrequency: 'daily',
      priority: 0.5,
    },
  ]
}

// 500건 하드캡을 offset으로 순회해 전체 발행 콘텐츠를 모은다 — 몇 년 누적되어 500편을
// 넘는 병원도 오래된 콘텐츠가 sitemap에서 사라지지 않도록 한다.
async function fetchAllContents(apiBase: string, slug: string): Promise<ContentEntry[]> {
  const all: ContentEntry[] = []
  let offset = 0
  for (;;) {
    let page: ContentEntry[]
    const url =
      offset === 0
        ? `${apiBase}/hospitals/${encodeURIComponent(slug)}/contents?limit=${CONTENT_PAGE_SIZE}`
        : `${apiBase}/hospitals/${encodeURIComponent(slug)}/contents?limit=${CONTENT_PAGE_SIZE}&offset=${offset}`
    try {
      // 첫 페이지(offset=0)는 lib/api.ts의 fetchContents(slug, 500)와 정확히 같은 URL
      // 모양(`?limit=500`, offset 파라미터 없음)을 써서 같은 Next data cache 키를
      // 공유한다 — offset=0을 명시하면 별도 키로 갈라져 같은 병원 콘텐츠를 캐시가
      // 두 번 들고 있게 된다.
      const res = await fetch(url, { cache: 'no-store' })
      if (res.status === 404) {
        throw new SitemapPrivateVisibilityError(
          `Hospital ${slug} became private while building its sitemap`,
        )
      }
      if (!res.ok) {
        throw new SitemapTransientError(
          `Contents upstream failed for ${slug} at offset ${offset}: HTTP ${res.status}`,
        )
      }
      const payload: unknown = await res.json()
      if (!Array.isArray(payload)) {
        throw new SitemapTransientError(
          `Invalid contents payload for ${slug} at offset ${offset}`,
        )
      }
      page = payload as ContentEntry[]
    } catch (err) {
      if (err instanceof SitemapPrivateVisibilityError || err instanceof SitemapTransientError) {
        throw err
      }
      throw new SitemapTransientError(
        `Contents upstream unavailable for ${slug} at offset ${offset}`,
        { cause: err },
      )
    }
    all.push(...page)
    if (page.length < CONTENT_PAGE_SIZE) break
    offset += CONTENT_PAGE_SIZE
  }
  return all
}

export async function appendHospitalEntries(
  entries: MetadataRoute.Sitemap,
  apiBase: string,
  hospital: HospitalEntry,
  scopeBase: string,
  hospitalPathPrefix: string,
): Promise<void> {
  const hospitalLastModified = validDate(hospital.updated_at)
  // Sitemap 프로토콜의 단일-host 계약: 응답을 제공한 scope의 origin으로만 URL을 만든다.
  // 페이지 canonical은 별도 신호이며, 플랫폼/커스텀/하이브리드 sitemap 간 origin을 섞지 않는다.
  //
  // 경로 접두어는 scope마다 다르다. 커스텀 도메인/하이브리드 서브도메인에서는 middleware가
  // `/{slug}` 접두어를 308로 떼어내므로(host-routing.decideCanonicalRedirect), 접두어를 붙이면
  // 제출한 URL 전부가 리다이렉트되고 페이지 canonical과도 어긋난다. 그래서 host scope는 ''를,
  // 플랫폼 scope는 `/{slug}`를 쓴다.
  const base = `${scopeBase}${hospitalPathPrefix}`

  // Treatment pillar pages (cluster hubs).
  // List endpoint returns minimal projection; pillar slugs need treatments[].
  // We fetch hospital detail only when the list response omits treatments.
  let treatments = hospital.treatments
  if (!treatments) {
    try {
      const detailRes = await fetch(`${apiBase}/hospitals/${encodeURIComponent(hospital.slug)}`, {
        cache: 'no-store',
      })
      if (detailRes.status === 404) {
        throw new SitemapPrivateVisibilityError(
          `Hospital ${hospital.slug} became private while building its sitemap`,
        )
      }
      if (!detailRes.ok) {
        throw new SitemapTransientError(
          `Hospital detail upstream failed for ${hospital.slug}: HTTP ${detailRes.status}`,
        )
      }
      const detail: unknown = await detailRes.json()
      if (typeof detail !== 'object' || detail === null) {
        throw new SitemapTransientError(`Invalid hospital detail for ${hospital.slug}`)
      }
      treatments = 'treatments' in detail && Array.isArray(detail.treatments)
        ? detail.treatments as Array<{ name: string }>
        : []
    } catch (err) {
      if (err instanceof SitemapPrivateVisibilityError || err instanceof SitemapTransientError) {
        throw err
      }
      throw new SitemapTransientError(`Hospital detail unavailable for ${hospital.slug}`, {
        cause: err,
      })
    }
  }

  // All upstream reads finish before mutating the caller's array. A later pagination failure
  // therefore cannot escape as a valid-looking prefix of the sitemap.
  const contents = await fetchAllContents(apiBase, hospital.slug)
  const completeEntries: MetadataRoute.Sitemap = [
    {
      url: base,
      ...(hospitalLastModified ? { lastModified: hospitalLastModified } : {}),
      changeFrequency: 'weekly',
      priority: 0.8,
    },
    {
      url: `${base}/contents`,
      ...(hospitalLastModified ? { lastModified: hospitalLastModified } : {}),
      changeFrequency: 'weekly',
      priority: 0.7,
    },
    {
      url: `${base}/doctor`,
      ...(hospitalLastModified ? { lastModified: hospitalLastModified } : {}),
      changeFrequency: 'monthly',
      priority: 0.6,
    },
    {
      url: `${base}/treatments`,
      ...(hospitalLastModified ? { lastModified: hospitalLastModified } : {}),
      changeFrequency: 'monthly',
      priority: 0.6,
    },
    {
      url: `${base}/visit`,
      ...(hospitalLastModified ? { lastModified: hospitalLastModified } : {}),
      changeFrequency: 'monthly',
      priority: 0.6,
    },
    {
      url: `${base}/llms.txt`,
      ...(hospitalLastModified ? { lastModified: hospitalLastModified } : {}),
      changeFrequency: 'daily',
      priority: 0.5,
    },
  ]
  for (const treatment of treatments ?? []) {
    const treatmentSlug = buildTreatmentSlug(treatment.name)
    if (!treatmentSlug) continue
    completeEntries.push({
      url: `${base}/treatments/${treatmentSlug}`,
      ...(hospitalLastModified ? { lastModified: hospitalLastModified } : {}),
      changeFrequency: 'weekly',
      priority: 0.7,
    })
  }

  // Hospital contents — 발행된 콘텐츠 전체(500건 하드캡을 offset으로 순회).
  for (const content of contents) {
    completeEntries.push({
      url: `${base}/contents/${content.id}`,
      lastModified:
        validDate(content.body_updated_at) ||
        validDate(content.published_at),
      changeFrequency: 'monthly',
      priority: 0.6,
    })
  }
  entries.push(...completeEntries)
}

function validDate(value: string | null | undefined): Date | undefined {
  if (!value) return undefined
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? undefined : parsed
}

// 커스텀 도메인 host를 병원 slug로 해석한다(미등록/조회 실패 시 null).
async function resolveHostSlug(apiBase: string, hostname: string): Promise<string | null> {
  try {
    const res = await fetch(
      `${apiBase}/site/hospitals/by-domain/${encodeURIComponent(hostname)}`,
      { cache: 'no-store' },
    )
    if (res.status === 404) return null
    if (!res.ok) {
      throw new SitemapTransientError(
        `Host resolution upstream failed for ${hostname}: HTTP ${res.status}`,
      )
    }
    const data: unknown = await res.json()
    if (typeof data === 'object' && data !== null && 'slug' in data && typeof data.slug === 'string') {
      return data.slug
    }
    throw new SitemapTransientError(`Invalid host resolution payload for ${hostname}`)
  } catch (err) {
    if (err instanceof SitemapTransientError) throw err
    throw new SitemapTransientError(`Host resolution unavailable for ${hostname}`, { cause: err })
  }
}

async function fetchHospitalDetail(apiBase: string, slug: string): Promise<HospitalEntry | null> {
  try {
    const detailRes = await fetch(`${apiBase}/hospitals/${encodeURIComponent(slug)}`, {
      cache: 'no-store',
    })
    if (detailRes.status === 404) return null
    if (!detailRes.ok) {
      throw new SitemapTransientError(
        `Hospital detail upstream failed for ${slug}: HTTP ${detailRes.status}`,
      )
    }
    const payload: unknown = await detailRes.json()
    if (typeof payload !== 'object' || payload === null || !('slug' in payload)) {
      throw new SitemapTransientError(`Invalid hospital detail payload for ${slug}`)
    }
    return payload as HospitalEntry
  } catch (err) {
    if (err instanceof SitemapTransientError) throw err
    throw new SitemapTransientError(`Hospital detail unavailable for ${slug}`, { cause: err })
  }
}

export async function buildSitemap(
  scope: SitemapScope,
  apiBase: string | null,
): Promise<MetadataRoute.Sitemap> {
  if (!apiBase) {
    // apiBase 미설정(서버 오설정): 커스텀 도메인이면 플랫폼 URL을 노출하지 않도록 빈 sitemap.
    if (scope.kind === 'host') {
      throw new SitemapTransientError('API base is missing for a tenant sitemap')
    }
    return platformBaseEntries()
  }

  if (scope.kind === 'host') {
    // 커스텀 도메인/하이브리드 서브도메인 sitemap에는 그 병원 URL만 싣는다 —
    // 플랫폼 엔트리(platformBaseEntries)는 넣지 않는다.
    const entries: MetadataRoute.Sitemap = []
    const slug = await resolveHostSlug(apiBase, scope.hostname)
    // 미등록 도메인/조회 실패 — 빈 sitemap(플랫폼/타 병원 URL을 노출하지 않는다).
    if (!slug) return entries

    const hospital = await fetchHospitalDetail(apiBase, slug)
    if (!hospital) return entries

    // host scope의 공개 경로에는 slug 접두어가 없다 — middleware가 `/{slug}`를 308로 떼어낸다.
    try {
      await appendHospitalEntries(entries, apiBase, hospital, `https://${scope.hostname}`, '')
    } catch (err) {
      // A tenant that becomes private during pagination must produce a complete empty sitemap,
      // never a cached prefix containing content that is no longer public.
      if (err instanceof SitemapPrivateVisibilityError) return []
      throw err
    }
    return entries
  }

  const entries = platformBaseEntries()
  return entries
}
