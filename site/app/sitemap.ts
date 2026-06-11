import { MetadataRoute } from 'next'

import { getApiBase } from '@/lib/config'
import { canonicalBase, platformSiteUrl } from '@/lib/site-url'
import { buildTreatmentSlug } from '@/lib/treatment-slug'

const SITE_URL = platformSiteUrl()

interface HospitalEntry {
  slug: string
  aeo_domain?: string | null
  updated_at?: string
  treatments?: Array<{ name: string }>
}

interface ContentEntry {
  id: string | number
  published_at: string | null
  scheduled_date: string
}

export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  const apiBase = getApiBase(false)

  // Always include the base URL as a fallback
  const entries: MetadataRoute.Sitemap = [
    {
      url: SITE_URL,
      lastModified: new Date(),
      changeFrequency: 'daily',
      priority: 1.0,
    },
    {
      url: `${SITE_URL}/llms.txt`,
      lastModified: new Date(),
      changeFrequency: 'daily',
      priority: 0.5,
    },
  ]

  if (!apiBase) {
    return entries
  }

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
    const hospitalLastModified = hospital.updated_at ? new Date(hospital.updated_at) : new Date()
    // 커스텀 도메인이 연결된 병원은 canonical인 그 도메인 URL을 sitemap에 싣는다.
    const base = canonicalBase(hospital)

    entries.push({
      url: `${base}/${hospital.slug}`,
      lastModified: hospitalLastModified,
      changeFrequency: 'weekly',
      priority: 0.8,
    })
    entries.push({
      url: `${base}/${hospital.slug}/contents`,
      lastModified: hospitalLastModified,
      changeFrequency: 'weekly',
      priority: 0.7,
    })
    entries.push({
      url: `${base}/${hospital.slug}/doctor`,
      lastModified: hospitalLastModified,
      changeFrequency: 'monthly',
      priority: 0.6,
    })
    entries.push({
      url: `${base}/${hospital.slug}/treatments`,
      lastModified: hospitalLastModified,
      changeFrequency: 'monthly',
      priority: 0.6,
    })
    entries.push({
      url: `${base}/${hospital.slug}/visit`,
      lastModified: hospitalLastModified,
      changeFrequency: 'monthly',
      priority: 0.6,
    })
    entries.push({
      url: `${base}/${hospital.slug}/llms.txt`,
      lastModified: hospitalLastModified,
      changeFrequency: 'daily',
      priority: 0.5,
    })

    // Treatment pillar pages (cluster hubs).
    // List endpoint returns minimal projection; pillar slugs need treatments[].
    // We fetch hospital detail only when the list response omits treatments.
    let treatments = hospital.treatments
    if (!treatments) {
      try {
        const detailRes = await fetch(`${apiBase}/hospitals/${hospital.slug}`, {
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
        lastModified: hospitalLastModified,
        changeFrequency: 'weekly',
        priority: 0.7,
      })
    }

    // Hospital contents — fetch all published content (up to 500)
    try {
      const cRes = await fetch(`${apiBase}/hospitals/${hospital.slug}/contents?limit=500`, {
        next: { revalidate: 3600 },
      })
      if (cRes.ok) {
        const contents: ContentEntry[] = await cRes.json()
        for (const content of contents) {
          entries.push({
            url: `${base}/${hospital.slug}/contents/${content.id}`,
            lastModified: content.published_at
              ? new Date(content.published_at)
              : new Date(content.scheduled_date),
            changeFrequency: 'monthly',
            priority: 0.6,
          })
        }
      } else {
        console.warn(`[sitemap] Failed to fetch contents for ${hospital.slug}: HTTP ${cRes.status}`)
      }
    } catch (err) {
      console.warn(`[sitemap] Error fetching contents for ${hospital.slug}:`, err)
    }
  }

  return entries
}
