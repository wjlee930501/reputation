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
  kakao_channel_url?: string
  google_business_profile_url?: string
  google_maps_url?: string
  naver_place_url?: string
  latitude?: number | null
  longitude?: number | null
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
  content_philosophy_id?: string | null
  query_target_id?: string | null
  exposure_action_id?: string | null
  content_brief?: Record<string, unknown> | null
  brief_status?: 'DRAFT' | 'APPROVED' | 'NEEDS_REVIEW' | null
  brief_approved_at?: string | null
  brief_approved_by?: string | null
  essence_status?: 'ALIGNED' | 'NEEDS_ESSENCE_REVIEW' | 'MISSING_APPROVED_PHILOSOPHY' | null
  essence_check_summary?: Record<string, unknown> | null
  body?: string | null
  image_prompt?: string | null
}

export type SourceType =
  | 'NAVER_BLOG'
  | 'YOUTUBE'
  | 'HOMEPAGE'
  | 'INTERVIEW'
  | 'LANDING_PAGE'
  | 'BROCHURE'
  | 'INTERNAL_NOTE'
  | 'OTHER'

export type SourceStatus = 'PENDING' | 'PROCESSED' | 'EXCLUDED' | 'ERROR'

export interface EvidenceNote {
  id: string
  hospital_id: string
  source_asset_id: string
  note_type: string
  claim: string
  source_excerpt: string
  excerpt_start: number | null
  excerpt_end: number | null
  confidence: number | null
  note_metadata: Record<string, unknown>
  created_at: string | null
}

export interface SourceAsset {
  id: string
  hospital_id: string
  source_type: SourceType
  title: string
  url: string | null
  raw_text?: string | null
  operator_note?: string | null
  source_metadata: Record<string, unknown>
  content_hash: string | null
  status: SourceStatus
  process_error: string | null
  processed_at: string | null
  created_by: string | null
  updated_by: string | null
  created_at: string | null
  updated_at: string | null
  evidence_note_count: number
  evidence_notes?: EvidenceNote[] | null
}

export interface ContentPhilosophy {
  id: string
  hospital_id: string
  version: number
  status: 'DRAFT' | 'APPROVED' | 'ARCHIVED'
  positioning_statement: string | null
  doctor_voice: string | null
  patient_promise: string | null
  content_principles: string[]
  tone_guidelines: string[]
  must_use_messages: string[]
  avoid_messages: string[]
  treatment_narratives: Array<Record<string, unknown>>
  local_context: Record<string, unknown>
  medical_ad_risk_rules: string[]
  evidence_map: Record<string, unknown>
  source_asset_ids: string[]
  unsupported_gaps: unknown[]
  conflict_notes: unknown[]
  synthesis_notes: string | null
  source_snapshot_hash: string | null
  created_by: string | null
  reviewed_by: string | null
  approved_at: string | null
  approval_note: string | null
  created_at: string | null
  updated_at: string | null
}

export type AIQueryTargetPriority = 'HIGH' | 'NORMAL' | 'LOW'
export type AIQueryTargetStatus = 'ACTIVE' | 'PAUSED' | 'ARCHIVED'

export interface AIQueryVariant {
  id: string
  query_target_id: string
  query_text: string
  platform: string
  language: string
  is_active: boolean
  query_matrix_id: string | null
  created_at: string | null
  updated_at: string | null
}

export interface AIQueryTargetSummary {
  variant_count: number
  active_variant_count: number
  linked_query_matrix_count: number
  latest_sov_pct: number | null
  last_measured_at: string | null
  gap_status: string | null
  next_action: string | null
}

export interface AIQueryTarget {
  id: string
  hospital_id: string
  name: string
  target_intent: string
  region_terms: string[]
  specialty: string | null
  condition_or_symptom: string | null
  treatment: string | null
  decision_criteria: string[]
  patient_language: string
  platforms: string[]
  competitor_names: string[]
  priority: AIQueryTargetPriority
  status: AIQueryTargetStatus
  target_month: string | null
  created_by: string | null
  updated_by: string | null
  created_at: string | null
  updated_at: string | null
  variants: AIQueryVariant[]
  summary: AIQueryTargetSummary
}

export interface MeasurementRun {
  id: string
  hospital_id: string
  run_label: string | null
  measurement_method: string
  status: string
  query_count: number
  success_count: number
  failure_count: number
  success_rate: number
  failure_rate: number
  started_at: string | null
  completed_at: string | null
  model_name: string | null
  search_mode: string | null
  config: Record<string, unknown> | null
  error_summary: Record<string, unknown> | null
  created_at: string | null
  updated_at: string | null
}

export type ExposureActionType = 'MEASUREMENT' | 'CONTENT' | 'SOURCE' | 'WEBBLOG_IA'
export type ExposureActionStatus = 'OPEN' | 'IN_PROGRESS' | 'BLOCKED' | 'COMPLETED' | 'CANCELLED' | 'ARCHIVED'

export interface ExposureActionQueryTarget {
  id: string
  name: string
  target_intent: string
  priority: AIQueryTargetPriority
  status: AIQueryTargetStatus
  target_month: string | null
}

export interface ExposureAction {
  id: string
  hospital_id: string
  query_target_id: string | null
  gap_id: string | null
  gap_type: string | null
  severity: string | null
  evidence: Record<string, unknown>
  action_type: ExposureActionType | string
  title: string
  description: string
  owner: string | null
  due_month: string | null
  status: ExposureActionStatus | string
  linked_content_id: string | null
  linked_content: ExposureActionContentSummary | null
  linked_report_id: string | null
  completed_at: string | null
  created_at: string | null
  updated_at: string | null
  query_target: ExposureActionQueryTarget | null
}

export interface ExposureActionContentSummary {
  id: string
  content_type: ContentItem['content_type']
  sequence_no: number
  total_count: number
  scheduled_date: string
  status: ContentItem['status']
  title: string | null
  query_target_id: string | null
  exposure_action_id: string | null
  brief_status: ContentItem['brief_status']
  brief_approved_at: string | null
  brief_approved_by: string | null
  content_brief: Record<string, unknown> | null
}

export interface ExposureActionCreateBriefResponse {
  action: ExposureAction
  content_item: ExposureActionContentSummary
  philosophy_gate: {
    has_approved_philosophy: boolean
    message: string | null
  }
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
  essence_summary?: Record<string, unknown> | null
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
  PLAN_16: '월 콘텐츠 16편',
  PLAN_12: '월 콘텐츠 12편',
  PLAN_8: '월 콘텐츠 8편',
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

export const QUERY_TARGET_PRIORITY_LABELS: Record<AIQueryTargetPriority, { label: string; color: string }> = {
  HIGH: { label: '높음', color: 'bg-red-50 text-red-700 border-red-200' },
  NORMAL: { label: '보통', color: 'bg-blue-50 text-blue-700 border-blue-200' },
  LOW: { label: '낮음', color: 'bg-slate-50 text-slate-600 border-slate-200' },
}

export const QUERY_TARGET_STATUS_LABELS: Record<AIQueryTargetStatus, { label: string; color: string }> = {
  ACTIVE: { label: '운영중', color: 'bg-emerald-50 text-emerald-700 border-emerald-200' },
  PAUSED: { label: '일시정지', color: 'bg-amber-50 text-amber-700 border-amber-200' },
  ARCHIVED: { label: '보관됨', color: 'bg-slate-100 text-slate-500 border-slate-200' },
}

export const EXPOSURE_ACTION_TYPE_LABELS: Record<string, { label: string; color: string }> = {
  MEASUREMENT: { label: '측정', color: 'bg-blue-50 text-blue-700 border-blue-200' },
  CONTENT: { label: '콘텐츠', color: 'bg-emerald-50 text-emerald-700 border-emerald-200' },
  SOURCE: { label: '출처', color: 'bg-amber-50 text-amber-700 border-amber-200' },
  WEBBLOG_IA: { label: '웹블로그 IA', color: 'bg-violet-50 text-violet-700 border-violet-200' },
}

export const EXPOSURE_ACTION_STATUS_LABELS: Record<string, { label: string; color: string }> = {
  OPEN: { label: '대기', color: 'bg-slate-50 text-slate-700 border-slate-200' },
  IN_PROGRESS: { label: '진행중', color: 'bg-blue-50 text-blue-700 border-blue-200' },
  BLOCKED: { label: '확인필요', color: 'bg-red-50 text-red-700 border-red-200' },
  COMPLETED: { label: '완료', color: 'bg-emerald-50 text-emerald-700 border-emerald-200' },
  CANCELLED: { label: '취소', color: 'bg-slate-100 text-slate-500 border-slate-200' },
  ARCHIVED: { label: '보관', color: 'bg-slate-100 text-slate-500 border-slate-200' },
}
