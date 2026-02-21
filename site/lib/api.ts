const BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000/api/v1/public'

export interface Hospital {
  id: number
  name: string
  slug: string
  plan: string
  address: string
  phone: string
  business_hours: Record<string, string>
  website_url: string | null
  blog_url: string | null
  region: string[]
  specialties: string[]
  keywords: string[]
  director_name: string
  director_career: string
  director_philosophy: string
  treatments: Array<{ name: string; description: string }>
  aeo_domain: string | null
}

export interface ContentItem {
  id: number
  hospital_id: number
  content_type: string
  sequence_no: number
  total_count: number
  title: string
  body: string
  image_url: string | null
  scheduled_date: string
  status: string
  generated_at: string
  published_at: string | null
}

export async function fetchHospital(slug: string): Promise<Hospital> {
  const res = await fetch(`${BASE}/hospitals/${slug}`, { next: { revalidate: 3600 } })
  if (!res.ok) throw new Error(`Hospital not found: ${slug}`)
  return res.json()
}

export async function fetchContents(slug: string): Promise<ContentItem[]> {
  const res = await fetch(`${BASE}/hospitals/${slug}/contents`, { next: { revalidate: 1800 } })
  if (!res.ok) return []
  return res.json()
}

export async function fetchContent(slug: string, contentId: string): Promise<ContentItem> {
  const res = await fetch(`${BASE}/hospitals/${slug}/contents/${contentId}`, { next: { revalidate: 1800 } })
  if (!res.ok) throw new Error(`Content not found: ${contentId}`)
  return res.json()
}

export const TYPE_LABELS: Record<string, string> = {
  FAQ: 'FAQ',
  DISEASE: '질환 가이드',
  TREATMENT: '시술 안내',
  COLUMN: '원장 칼럼',
  HEALTH: '건강 정보',
  LOCAL: '지역 특화',
  NOTICE: '공지',
}
