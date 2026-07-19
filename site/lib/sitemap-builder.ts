// sitemap.ts의 엔트리 조립 로직 — 요청 컨텍스트(next/headers)에 의존하지 않는 순수/네트워크
// 계층으로 분리해 단위 테스트가 가능하게 한다(sitemap.ts는 headers()→scope만 넘기는 얇은 래퍼).
//
// 스코프별 계약:
//   - 'all'  : 플랫폼(전체 병원) sitemap. 플랫폼 루트 + /llms.txt(platformBaseEntries) +
//              모든 병원 엔트리.
//   - 'host' : 커스텀 도메인(또는 {slug}.{platform host} 하이브리드 서브도메인) sitemap.
//              그 병원 하나의 URL만 싣는다 — 플랫폼 엔트리는 절대 넣지 않는다(넣으면
//              커스텀 도메인 sitemap에 플랫폼/타 병원 URL이 함께 노출된다).

import type { MetadataRoute } from 'next'

import type { SitemapScope } from './sitemap-host.ts'
import { platformSiteUrl } from './site-url.ts'
import { buildTreatmentSlug } from './treatment-slug.ts'

// 백엔드 /contents 목록의 하드캡과 동일 — offset으로 페이지를 넘겨 전체 발행 콘텐츠를 순회한다.
const CONTENT_PAGE_SIZE = 500

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
    try {
      const res = await fetch(
        `${apiBase}/hospitals/${encodeURIComponent(slug)}/contents?limit=${CONTENT_PAGE_SIZE}&offset=${offset}`,
        { next: { revalidate: 3600 } },
      )
      if (!res.ok) {
        console.warn(
          `[sitemap] Failed to fetch contents for ${slug} at offset ${offset}: HTTP ${res.status}`,
        )
        break
      }
      page = await res.json()
    } catch (err) {
      console.warn(`[sitemap] Error fetching contents for ${slug} at offset ${offset}:`, err)
      break
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
): Promise<void> {
  const hospitalLastModified = validDate(hospital.updated_at)
  // Sitemap 프로토콜의 단일-host 계약: 응답을 제공한 scope의 origin으로만 URL을 만든다.
  // 페이지 canonical은 별도 신호이며, 플랫폼/커스텀/하이브리드 sitemap 간 origin을 섞지 않는다.
  const base = scopeBase

  entries.push({
    url: `${base}/${hospital.slug}`,
    ...(hospitalLastModified ? { lastModified: hospitalLastModified } : {}),
    changeFrequency: 'weekly',
    priority: 0.8,
  })
  entries.push({
    url: `${base}/${hospital.slug}/contents`,
    ...(hospitalLastModified ? { lastModified: hospitalLastModified } : {}),
    changeFrequency: 'weekly',
    priority: 0.7,
  })
  entries.push({
    url: `${base}/${hospital.slug}/doctor`,
    ...(hospitalLastModified ? { lastModified: hospitalLastModified } : {}),
    changeFrequency: 'monthly',
    priority: 0.6,
  })
  entries.push({
    url: `${base}/${hospital.slug}/treatments`,
    ...(hospitalLastModified ? { lastModified: hospitalLastModified } : {}),
    changeFrequency: 'monthly',
    priority: 0.6,
  })
  entries.push({
    url: `${base}/${hospital.slug}/visit`,
    ...(hospitalLastModified ? { lastModified: hospitalLastModified } : {}),
    changeFrequency: 'monthly',
    priority: 0.6,
  })
  entries.push({
    url: `${base}/${hospital.slug}/llms.txt`,
    ...(hospitalLastModified ? { lastModified: hospitalLastModified } : {}),
    changeFrequency: 'daily',
    priority: 0.5,
  })

  // Treatment pillar pages (cluster hubs).
  // List endpoint returns minimal projection; pillar slugs need treatments[].
  // We fetch hospital detail only when the list response omits treatments.
  let treatments = hospital.treatments
  if (!treatments) {
    try {
      const detailRes = await fetch(`${apiBase}/hospitals/${encodeURIComponent(hospital.slug)}`, {
        next: { revalidate: 3600 },
      })
      if (detailRes.ok) {
        const detail = await detailRes.json()
        treatments = detail.treatments ?? []
      }
    } catch {
      treatments = []
    }
  }
  for (const treatment of treatments ?? []) {
    const treatmentSlug = buildTreatmentSlug(treatment.name)
    if (!treatmentSlug) continue
    entries.push({
      url: `${base}/${hospital.slug}/treatments/${treatmentSlug}`,
      ...(hospitalLastModified ? { lastModified: hospitalLastModified } : {}),
      changeFrequency: 'weekly',
      priority: 0.7,
    })
  }

  // Hospital contents — 발행된 콘텐츠 전체(500건 하드캡을 offset으로 순회).
  const contents = await fetchAllContents(apiBase, hospital.slug)
  for (const content of contents) {
    entries.push({
      url: `${base}/${hospital.slug}/contents/${content.id}`,
      lastModified:
        validDate(content.body_updated_at) ||
        validDate(content.published_at) ||
        validDate(content.scheduled_date),
      changeFrequency: 'monthly',
      priority: 0.6,
    })
  }
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
      { next: { revalidate: 300 } },
    )
    if (res.ok) {
      const data: unknown = await res.json()
      return typeof data === 'object' && data !== null && 'slug' in data && typeof data.slug === 'string'
        ? data.slug
        : null
    }
  } catch (err) {
    console.error(`[sitemap] Error resolving host ${hostname} to a hospital:`, err)
  }
  return null
}

async function fetchHospitalDetail(apiBase: string, slug: string): Promise<HospitalEntry | null> {
  try {
    const detailRes = await fetch(`${apiBase}/hospitals/${encodeURIComponent(slug)}`, {
      next: { revalidate: 3600 },
    })
    if (detailRes.ok) return (await detailRes.json()) as HospitalEntry
  } catch (err) {
    console.error(`[sitemap] Error fetching hospital detail for ${slug}:`, err)
  }
  return null
}

export async function buildSitemap(
  scope: SitemapScope,
  apiBase: string | null,
): Promise<MetadataRoute.Sitemap> {
  if (!apiBase) {
    // apiBase 미설정(서버 오설정): 커스텀 도메인이면 플랫폼 URL을 노출하지 않도록 빈 sitemap.
    return scope.kind === 'host' ? [] : platformBaseEntries()
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

    await appendHospitalEntries(entries, apiBase, hospital, `https://${scope.hostname}`)
    return entries
  }

  const platformBase = platformSiteUrl()
  const entries = platformBaseEntries()
  let hospitals: HospitalEntry[]
  try {
    const res = await fetch(`${apiBase}/hospitals`, { next: { revalidate: 3600 } })
    if (!res.ok) {
      console.error(`[sitemap] Failed to fetch hospitals: HTTP ${res.status}`)
      return entries
    }
    hospitals = await res.json()
  } catch (err) {
    console.error('[sitemap] Error fetching hospitals:', err)
    return entries
  }

  for (const hospital of hospitals) {
    await appendHospitalEntries(entries, apiBase, hospital, platformBase)
  }

  return entries
}
