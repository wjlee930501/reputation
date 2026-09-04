import type { LeadDiagnosisSummary } from '@/lib/lead-diagnosis-status'

export type { LeadDiagnosisSummary }

export type PlanCode = 'PLAN_20' | 'PLAN_16' | 'PLAN_12'
export type HandoffState = 'CONTRACT_PENDING' | 'CONTRACTED' | 'HANDOFF_ACCEPTED'

export interface Handoff {
  id: string
  hospital_id: string
  hospital_name?: string | null
  state: HandoffState
  sales_owner_id?: string | null
  ae_owner_id?: string | null
  sales_owner_name?: string | null
  ae_owner_name?: string | null
  contract_reference?: string | null
  contract_effective_at?: string | null
  plan?: PlanCode | null
  sla_due_at?: string | null
  accepted_by_id?: string | null
  accepted_by_name?: string | null
  accepted_at?: string | null
  version: number
  next_action?: string
}

export interface AdminAccountSummary {
  id: string
  email: string
  name: string
  role: 'OWNER' | 'OPERATOR'
  is_active: boolean
  is_operations_test?: boolean
}

export interface Hospital {
  id: string
  name: string
  slug: string
  status: 'ONBOARDING' | 'ANALYZING' | 'BUILDING' | 'PENDING_DOMAIN' | 'ACTIVE' | 'PAUSED'
  plan: 'PLAN_20' | 'PLAN_16' | 'PLAN_12' | null
  profile_complete: boolean
  /** 승인이 남은 공개 표면 시각 항목 라벨. 비어 있으면 승인 완료(O-2). */
  visual_approval_missing?: string[]
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
  domain_management_mode?: 'HOSPITAL_MANAGED' | 'MOTIONLABS_MANAGED'
  domain_dns_strategy?: 'CNAME' | 'APEX_ADDRESS'
  domain_registrar?: string | null
  domain_dns_provider?: string | null
  domain_purchase_note?: string | null
  domain_cert_dns_verified_at?: string | null
  domain_cert_job_state?: string | null
  // 마지막으로 공개 주소에 실제 요청을 보내 확인한 결과. domain_cert_* 는 도메인
  // 재저장 시 초기화되므로, 살아 있는 도메인의 진실은 이쪽에 남는다.
  domain_last_checked_at?: string | null
  domain_last_check_ok?: boolean | null
  domain_last_check_reason?: string | null
  region?: string[]
  specialties?: string[]
  keywords?: string[]
  competitors?: string[]
  director_name?: string
  director_career?: string
  director_philosophy?: string
  treatments?: Array<{ name: string; description: string }>
  // 아래 9개는 병원 기본 정보(profile) 편집 폼 전용 필드 — GET /admin/hospitals/{id}는
  // 이미 이 값들을 함께 내려주므로, 헤더 컨텍스트가 같은 응답을 재사용할 수 있게
  // 여기서도 선언해 둔다 (profile 페이지의 중복 fetch 제거).
  brand_primary_color?: string | null
  brand_accent_color?: string | null
  logo_url?: string | null
  hero_image_url?: string | null
  hero_media_kind?: 'VERIFIED_FACILITY' | 'BRAND_GRAPHIC' | '' | null
  hero_headline?: string | null
  hero_description?: string | null
  image_style_direction?: string | null
  site_access_mode?: 'urgent' | 'appointment' | 'specialist' | '' | null
}

export interface ContentReference {
  title: string
  url: string
  publisher?: string | null
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
  status: 'DRAFT' | 'READY' | 'PUBLISHED' | 'REJECTED' | 'CANCELLED'
  // 반려 슬롯이 월 경계를 넘어 이월된 경우의 원래 예정일 — 다음 달 최우선 처리 대상
  carried_over_from?: string | null
  references?: ContentReference[]
  faq_question?: string | null
  faq_answer_summary?: string | null
  body_updated_at?: string | null
  display?: {
    content_type_label?: string | null
    status_label?: string | null
    brief_status_label?: string | null
    essence_status_label?: string | null
    review?: {
      label?: string | null
      reason?: string | null
      publishable?: boolean | null
    } | null
  }
  generated_at: string | null
  published_at: string | null
  published_by: string | null
  post_publish_notified_at?: string | null
  post_publish_reviewed_at?: string | null
  post_publish_reviewed_by?: string | null
  content_philosophy_id?: string | null
  query_target_id?: string | null
  exposure_action_id?: string | null
  content_brief?: Record<string, unknown> | null
  brief_status?: 'DRAFT' | 'APPROVED' | 'NEEDS_REVIEW' | null
  brief_approved_at?: string | null
  brief_approved_by?: string | null
  essence_status?: 'ALIGNED' | 'NEEDS_ESSENCE_REVIEW' | 'MISSING_APPROVED_PHILOSOPHY' | null
  essence_check_summary?: Record<string, unknown> | null
  // 발행 가능 여부의 단일 기준 — backend _serialize_item이 목록/상세 모두 항상 직렬화한다.
  compliance: {
    status: 'PASS' | 'BLOCKED'
    publishable: boolean
    blockers: string[]
    forbidden_violations: string[]
    references_count?: number
    essence_status?: string | null
    essence_check_summary?: Record<string, unknown> | null
  }
  body?: string | null
  image_prompt?: string | null
}

// backend/app/api/admin/essence.py SOURCE_TYPE_DISPLAY_LABELS와 동기화
export type SourceType =
  | 'NAVER_BLOG'
  | 'YOUTUBE'
  | 'HOMEPAGE'
  | 'INTERVIEW'
  | 'LANDING_PAGE'
  | 'BROCHURE'
  | 'INTERNAL_NOTE'
  | 'PHOTO_DOCTOR'
  | 'PHOTO_CLINIC_EXTERIOR'
  | 'PHOTO_CLINIC_INTERIOR'
  | 'PHOTO_TREATMENT_ROOM'
  | 'PHOTO_BRAND'
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
  display?: {
    source_type_label?: string | null
    status_label?: string | null
  }
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
  file_url: string | null
  file_access_url: string | null
  mime_type: string | null
  file_size_bytes: number | null
  is_public: boolean
}

export interface ContentPhilosophy {
  id: string
  hospital_id: string
  version: number
  status: 'DRAFT' | 'APPROVED' | 'ARCHIVED'
  display?: {
    status_label?: string | null
  }
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
  display?: {
    platform_label?: string | null
    status_label?: string | null
  }
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
  display?: {
    priority_label?: string | null
    status_label?: string | null
    platform_labels?: string[] | null
  }
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
  display?: {
    measurement_method_label?: string | null
    status_label?: string | null
  }
  query_count: number
  success_count: number
  ambiguous_count?: number
  failure_count: number
  // 측정 건이 0이면 비율을 만들 수 없다 — null은 '산출 불가'이며 0%와 다르다.
  success_rate: number | null
  failure_rate: number | null
  started_at: string | null
  completed_at: string | null
  model_name: string | null
  search_mode: string | null
  config: Record<string, unknown> | null
  error_summary: Record<string, unknown> | null
  created_at: string | null
  updated_at: string | null
}

export interface OperationResponse {
  detail?: string
  hospital_id?: string
  content_id?: string
  domain?: string
  verified?: boolean
  cname_value?: string | null
  expected_cname?: string
  operation_run_id?: string
  operation_state?: string
  task_id?: string
  idempotent_replay?: boolean
}

export interface SalesLead {
  id: string
  clinic_name: string
  clinic_type: string
  contact: string
  question: string
  privacy: boolean
  source_path: string | null
  /** 유입 퍼널 — 문의 폼(INQUIRY) 또는 무료 진단(AI_DIAGNOSIS) */
  source?: string | null
  /** 운영 점검용 픽스처로 만들어진 요청 */
  is_operations_test?: boolean
  status?: 'NEW' | 'CONTACTED' | 'CONVERTED' | 'DISMISSED' | string
  converted_hospital_id?: string | null
  converted_at?: string | null
  conversion_note?: string | null
  notification_status?: 'SENT' | 'FAILED' | string | null
  notification_error?: string | null
  created_at: string | null
  /** 무료 진단(1단) 요약 — 리드마그넷으로 들어온 리드에만 있다. */
  diagnoses?: LeadDiagnosisSummary[]
}

export type ExposureActionType = 'MEASUREMENT' | 'CONTENT' | 'SOURCE' | 'WEBBLOG_IA'
export type ExposureActionStatus = 'OPEN' | 'IN_PROGRESS' | 'BLOCKED' | 'COMPLETED' | 'CANCELLED' | 'ARCHIVED'

export interface ExposureActionQueryTarget {
  id: string
  name: string
  target_intent: string
  priority: AIQueryTargetPriority
  status: AIQueryTargetStatus
  display?: {
    priority_label?: string | null
    status_label?: string | null
  }
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
  display?: {
    action_type_label?: string | null
    status_label?: string | null
    gap_type_label?: string | null
    severity_label?: string | null
    evidence_summary?: string | null
    evidence_items?: Array<{ key: string; label: string; value: string }> | null
  }
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

// GET /admin/hospitals/{id}/schedule — 활성 스케줄 (없으면 404)
export interface ScheduleInfo {
  plan: 'PLAN_20' | 'PLAN_16' | 'PLAN_12'
  publish_days: number[]
  active_from: string
  is_active: boolean
}

export const STATUS_LABELS: Record<string, { label: string; color: string }> = {
  ONBOARDING: { label: '온보딩 진행 중', color: 'bg-gray-100 text-gray-700' },
  ANALYZING: { label: 'AI 진단 분석 중', color: 'bg-blue-100 text-blue-700' },
  BUILDING: { label: '콘텐츠 허브 준비 중', color: 'bg-orange-100 text-orange-700' },
  PENDING_DOMAIN: { label: '공개 주소 확인 대기', color: 'bg-yellow-100 text-yellow-700' },
  ACTIVE: { label: '운영 중', color: 'bg-green-100 text-green-700' },
  PAUSED: { label: '운영 일시 정지', color: 'bg-red-100 text-red-700' },
}

export const PLAN_LABELS: Record<string, string> = {
  PLAN_12: '스타터 · 월 12편',
  PLAN_16: '그로워 · 월 16편',
  PLAN_20: '리더 · 월 20편',
}

export const PLAN_CONTRACT_LABELS: Record<string, string> = {
  PLAN_12: '스타터 · 월 12편 · 60만원 (부가세 별도)',
  PLAN_16: '그로워 · 월 16편 · 90만원 (부가세 별도)',
  PLAN_20: '리더 · 월 20편 · 120만원 (부가세 별도)',
}

export const TYPE_LABELS: Record<string, string> = {
  FAQ: '자주 묻는 질문',
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
  ACTIVE: { label: '운영 중', color: 'bg-emerald-50 text-emerald-700 border-emerald-200' },
  PAUSED: { label: '운영 일시 정지', color: 'bg-amber-50 text-amber-700 border-amber-200' },
  ARCHIVED: { label: '보관됨', color: 'bg-slate-100 text-slate-500 border-slate-200' },
}

export const EXPOSURE_ACTION_TYPE_LABELS: Record<string, { label: string; color: string }> = {
  MEASUREMENT: { label: '측정', color: 'bg-blue-50 text-blue-700 border-blue-200' },
  CONTENT: { label: '콘텐츠', color: 'bg-emerald-50 text-emerald-700 border-emerald-200' },
  SOURCE: { label: '출처', color: 'bg-amber-50 text-amber-700 border-amber-200' },
  WEBBLOG_IA: { label: '정보 구조', color: 'bg-violet-50 text-violet-700 border-violet-200' },
}

export const EXPOSURE_ACTION_STATUS_LABELS: Record<string, { label: string; color: string }> = {
  OPEN: { label: '대기', color: 'bg-slate-50 text-slate-700 border-slate-200' },
  IN_PROGRESS: { label: '진행중', color: 'bg-blue-50 text-blue-700 border-blue-200' },
  BLOCKED: { label: '확인필요', color: 'bg-red-50 text-red-700 border-red-200' },
  COMPLETED: { label: '완료', color: 'bg-emerald-50 text-emerald-700 border-emerald-200' },
  CANCELLED: { label: '취소', color: 'bg-slate-100 text-slate-500 border-slate-200' },
  ARCHIVED: { label: '보관', color: 'bg-slate-100 text-slate-500 border-slate-200' },
}

export type OperationsQueue = 'ONBOARDING' | 'TODAY' | 'REPORTS' | 'INCIDENTS'
export type OperationsQueueParam = 'onboarding' | 'today' | 'reports' | 'incidents'
export type OperationsSeverity = 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL'
export type OperationsSlaState = 'NONE' | 'DUE' | 'OVERDUE'
export type OperationsRunState =
  | 'REQUESTED'
  | 'QUEUED'
  | 'RUNNING'
  | 'SUCCEEDED'
  | 'PARTIAL'
  | 'FAILED'
  | 'CANCELLED'
export type OperationsSlackState =
  | 'PENDING'
  | 'SENDING'
  | 'RETRYING'
  | 'HOLD'
  | 'SENT'
  | 'FAILED'

export interface OperationsOwner {
  readonly id: string
  readonly name: string
  readonly email: string
}

export interface OperationsAction {
  readonly kind: string
  readonly label: string
  readonly method: 'GET' | 'POST'
  readonly path: string
  readonly enabled: boolean
  readonly reason_required?: boolean
  readonly requires_version?: boolean
  readonly requires_idempotency_key?: boolean
}

export interface OperationsSlack {
  readonly notification_id: string
  readonly notification_type: string
  readonly state: OperationsSlackState
  readonly attempt_count: number
  readonly max_attempts: number
  readonly next_attempt_at: string | null
  readonly sent_at: string | null
  readonly safe_error_code: string | null
  readonly safe_error_message: string | null
  readonly version: number
}

export interface OperationsHistoryEntry {
  readonly event: string
  readonly at: string
  readonly actor?: string | null
}

export interface OperationsQueueRow {
  readonly id: string
  readonly queue: OperationsQueue
  readonly customer: {
    readonly hospital_id: string | null
    readonly name: string
    readonly admin_path: string
  }
  readonly status: string
  readonly severity: OperationsSeverity
  readonly impact: string
  readonly owner: OperationsOwner | null
  readonly sla_due_at: string | null
  readonly sla_state: OperationsSlaState
  readonly days_since_close?: number | null
  readonly next_action: string
  readonly action: OperationsAction
  readonly retry: OperationsAction | null
  readonly cause_code: string | null
  readonly cause_message: string | null
  readonly cause_group_key: string | null
  readonly same_type_count: number
  readonly affected_hospital_count: number
  readonly cost_guard_category: string | null
  /**
   * False marks a row that is context, not work — automatic recovery owns it
   * (RETRYING), or the normal schedule has not reached it yet. Render it in a
   * collapsed/secondary state instead of hiding it.
   */
  readonly requires_operator_action?: boolean
  readonly safe_cause: string | null
  readonly history: readonly OperationsHistoryEntry[]
  readonly slack: OperationsSlack | null
  readonly incident_id: string | null
  readonly operation_run_id: string | null
  readonly content_id: string | null
  readonly report_id: string | null
  readonly version: number | null
  readonly occurred_at: string
}

export interface OperationsQueueResponse {
  readonly queue: OperationsQueue
  readonly total: number
  readonly page: number
  readonly page_size: number
  readonly items: readonly OperationsQueueRow[]
}

export interface OperationsOverviewResponse {
  readonly queues: readonly {
    readonly queue: OperationsQueue
    readonly total: number
    readonly overdue: number
  }[]
  readonly items: readonly OperationsQueueRow[]
}

export interface OperationsRunSummary {
  readonly run_id: string
  readonly parent_run_id: string | null
  readonly operation_type: string
  readonly state: OperationsRunState
  readonly attempt_count: number
  readonly total_count: number
  readonly success_count: number
  readonly failure_count: number
  readonly skipped_count: number
  readonly safe_error_code: string | null
  readonly safe_error_message: string | null
  readonly requested_at: string
  readonly queued_at: string | null
  readonly started_at: string | null
  readonly completed_at: string | null
  readonly version: number
  readonly retry: OperationsAction | null
}

export interface OperationsIncidentDetail {
  readonly incident: OperationsQueueRow
  readonly run: OperationsRunSummary | null
}
