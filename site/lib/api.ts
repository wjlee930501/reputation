import { getApiBase } from '@/lib/config'

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
  kakao_channel_url: string | null
  google_business_profile_url: string | null
  google_maps_url: string | null
  naver_place_url: string | null
  latitude: number | null
  longitude: number | null
  region: string[]
  specialties: string[]
  keywords: string[]
  director_name: string
  director_career: string
  director_philosophy: string | null
  director_photo_url: string | null
  treatments: Array<{ name: string; description: string }>
  aeo_domain: string | null
  photos: HospitalPhoto[]
}

export type HospitalPhotoType =
  | 'PHOTO_DOCTOR'
  | 'PHOTO_CLINIC_EXTERIOR'
  | 'PHOTO_CLINIC_INTERIOR'
  | 'PHOTO_TREATMENT_ROOM'

export interface HospitalPhoto {
  id: string
  source_type: HospitalPhotoType
  title: string
  url: string
}

const ASSETS_BACKEND_BASE =
  process.env.NEXT_PUBLIC_BACKEND_URL || process.env.BACKEND_URL || 'http://localhost:8000'

// 백엔드가 GCS 미설정 환경에서 file_url을 "/assets/..."로 반환. 절대 URL로 전환해
// /site(다른 호스트)에서도 이미지 로드 가능하게 한다.
export function resolveAssetUrl(url: string | null | undefined): string | null {
  if (!url) return null
  if (url.startsWith('http://') || url.startsWith('https://')) return url
  if (url.startsWith('/')) return `${ASSETS_BACKEND_BASE}${url}`
  return url
}

export interface ContentReference {
  title: string
  url: string
}

export interface ContentItem {
  id: number
  hospital_id: number
  content_type: string
  sequence_no: number
  total_count: number
  title: string
  body: string
  meta_description: string | null
  image_url: string | null
  scheduled_date: string
  status: string
  generated_at: string
  published_at: string | null
  body_updated_at: string | null
  references: ContentReference[]
  faq_question: string | null
  faq_answer_summary: string | null
}

export async function fetchHospital(slug: string): Promise<Hospital> {
  const res = await fetch(`${getApiBase()}/hospitals/${slug}`, { next: { revalidate: 3600 } })
  if (!res.ok) throw new Error(`Hospital not found: ${slug}`)
  return res.json()
}

export async function fetchContents(slug: string, limit?: number): Promise<ContentItem[]> {
  const base = getApiBase()
  const url = limit
    ? `${base}/hospitals/${slug}/contents?limit=${limit}`
    : `${base}/hospitals/${slug}/contents`
  const res = await fetch(url, { next: { revalidate: 1800 } })
  if (!res.ok) return []
  return res.json()
}

export async function fetchContent(slug: string, contentId: string): Promise<ContentItem> {
  const res = await fetch(`${getApiBase()}/hospitals/${slug}/contents/${contentId}`, { next: { revalidate: 1800 } })
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
