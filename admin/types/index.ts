export interface Hospital {
  id: string
  name: string
  slug: string
  status: 'ONBOARDING' | 'ANALYZING' | 'BUILDING' | 'PENDING_DOMAIN' | 'ACTIVE' | 'PAUSED'
  plan: 'PLAN_16' | 'PLAN_12' | 'PLAN_8' | null
  profile_complete: boolean
  v0_report_done: boolean
  site_built?: boolean
  site_live: boolean
  schedule_set: boolean
  created_at: string | null
  address?: string
  phone?: string
  business_hours?: Record<string, string>
  website_url?: string
  blog_url?: string
  aeo_domain?: string
  region?: string[]
  specialties?: string[]
  keywords?: string[]
  competitors?: string[]
  director_name?: string
  director_career?: string
  director_philosophy?: string
  treatments?: Array<{ name: string; description: string }>
}

export interface ContentItem {
  id: string
  content_type: 'FAQ' | 'DISEASE' | 'TREATMENT' | 'COLUMN' | 'HEALTH' | 'LOCAL' | 'NOTICE'
  sequence_no: number
  total_count: number
  title: string | null
  meta_description: string | null
  image_url: string | null
  scheduled_date: string
  status: 'DRAFT' | 'READY' | 'PUBLISHED' | 'REJECTED'
  generated_at: string | null
  published_at: string | null
  published_by: string | null
  body?: string | null
  image_prompt?: string | null
}

export interface Report {
  id: string
  hospital_id: string
  period_year: number
  period_month: number
  report_type: 'V0' | 'MONTHLY'
  pdf_path: string | null
  sov_summary: Record<string, unknown> | null
  content_summary: Record<string, unknown> | null
  created_at: string
  sent_at: string | null
}

export const STATUS_LABELS: Record<string, { label: string; color: string }> = {
  ONBOARDING: { label: '온보딩', color: 'bg-gray-100 text-gray-700' },
  ANALYZING: { label: '분석중', color: 'bg-blue-100 text-blue-700' },
  BUILDING: { label: '빌드중', color: 'bg-orange-100 text-orange-700' },
  PENDING_DOMAIN: { label: '도메인대기', color: 'bg-yellow-100 text-yellow-700' },
  ACTIVE: { label: '운영중', color: 'bg-green-100 text-green-700' },
  PAUSED: { label: '일시정지', color: 'bg-red-100 text-red-700' },
}

export const PLAN_LABELS: Record<string, string> = {
  PLAN_16: '16편/월',
  PLAN_12: '12편/월',
  PLAN_8: '8편/월',
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
