import { MetadataRoute } from 'next'

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000/api/v1/public'
const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL || 'https://reputation.co.kr'

interface HospitalEntry {
  slug: string
  updated_at?: string
}

interface ContentEntry {
  id: string | number
  published_at: string | null
  scheduled_date: string
}

export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  const entries: MetadataRoute.Sitemap = []

  try {
    const res = await fetch(`${API_BASE}/hospitals`, { next: { revalidate: 3600 } })
    if (!res.ok) return entries

    const hospitals: HospitalEntry[] = await res.json()

    for (const hospital of hospitals) {
      // Hospital main page
      entries.push({
        url: `${SITE_URL}/${hospital.slug}`,
        lastModified: hospital.updated_at ? new Date(hospital.updated_at) : new Date(),
        changeFrequency: 'weekly',
        priority: 0.8,
      })

      // Hospital contents
      try {
        const cRes = await fetch(`${API_BASE}/hospitals/${hospital.slug}/contents`, {
          next: { revalidate: 3600 },
        })
        if (cRes.ok) {
          const contents: ContentEntry[] = await cRes.json()
          for (const content of contents) {
            entries.push({
              url: `${SITE_URL}/${hospital.slug}/contents/${content.id}`,
              lastModified: content.published_at
                ? new Date(content.published_at)
                : new Date(content.scheduled_date),
              changeFrequency: 'monthly',
              priority: 0.6,
            })
          }
        }
      } catch {
        // skip this hospital's contents on error
      }
    }
  } catch {
    // return empty sitemap on error
  }

  return entries
}
