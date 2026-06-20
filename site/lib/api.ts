import { getApiBase } from './config.ts'
import { publicFetchInit } from './fetch-policy.ts'
import { parseHospitalPayload, type Hospital } from './hospital-payload.ts'
export { resolveAssetUrl } from './hospital-payload.ts'
export type { DirectorCredentials, Hospital, HospitalPhoto, HospitalPhotoType } from './hospital-payload.ts'

export type ContentReferenceSourceType =
  | 'GOV_KR'
  | 'ACADEMIC_KR'
  | 'GOV_GLOBAL'
  | 'CLINIC_REFERENCE'
  | 'ENCYCLOPEDIA'

export interface ContentReference {
  title: string
  url: string
  source_type?: ContentReferenceSourceType | null
}

export const SOURCE_TYPE_LABELS: Record<ContentReferenceSourceType, string> = {
  GOV_KR: '한국 공공',
  ACADEMIC_KR: '한국 학술',
  GOV_GLOBAL: '국제 기관',
  CLINIC_REFERENCE: '임상 자료',
  ENCYCLOPEDIA: '백과',
}

// 목록 엔드포인트(/hospitals/{slug}/contents)가 실제로 반환하는 필드만 선언한다.
// body는 상세 엔드포인트에서만 내려오므로 ContentDetail로 분리.
export interface ContentSummary {
  id: string
  content_type: string
  title: string
  meta_description: string | null
  image_url: string | null
  scheduled_date: string
  published_at: string | null
  body_updated_at: string | null
  references: ContentReference[]
  faq_question: string | null
  faq_answer_summary: string | null
  // 서버에서 본문 길이로 계산해 내려주는 읽기 시간(분). 구버전 응답 캐시 대비 optional.
  reading_minutes?: number
}

export interface ContentDetail extends ContentSummary {
  body: string
}

export class HospitalNotFoundError extends Error {
  constructor(slug: string) {
    super(`Hospital not found: ${slug}`)
    this.name = 'HospitalNotFoundError'
  }
}

export class ContentNotFoundError extends Error {
  constructor(contentId: string) {
    super(`Content not found: ${contentId}`)
    this.name = 'ContentNotFoundError'
  }
}

export async function fetchHospital(slug: string): Promise<Hospital> {
  // 경로 세그먼트는 항상 인코딩 — 라우트 파라미터는 URL 디코드된 값이라 ?/#/%2F 류가
  // 백엔드 요청의 쿼리·경로로 주입될 수 있다 (admin BFF buildSafeAdminProxyPath와 동일 정책).
  const res = await fetch(`${getApiBase()}/hospitals/${encodeURIComponent(slug)}`, publicFetchInit(3600))
  if (res.status === 404) throw new HospitalNotFoundError(slug)
  if (!res.ok) throw new Error(`Server error (${res.status}) when fetching hospital`)
  const hospital = parseHospitalPayload(await res.json())
  if (!hospital) {
    throw new Error('Invalid hospital payload')
  }
  return hospital
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isNullableString(value: unknown): value is string | null {
  return value === null || typeof value === 'string'
}

function isContentReferencePayload(value: unknown): value is ContentReference {
  if (!isRecord(value)) return false
  return (
    typeof value.title === 'string' &&
    typeof value.url === 'string' &&
    (value.source_type === undefined || value.source_type === null || typeof value.source_type === 'string')
  )
}

function isContentSummaryPayload(value: unknown): value is ContentSummary {
  if (!isRecord(value)) return false
  return (
    typeof value.id === 'string' &&
    typeof value.content_type === 'string' &&
    typeof value.title === 'string' &&
    isNullableString(value.meta_description) &&
    isNullableString(value.image_url) &&
    typeof value.scheduled_date === 'string' &&
    isNullableString(value.published_at) &&
    isNullableString(value.body_updated_at) &&
    Array.isArray(value.references) &&
    value.references.every(isContentReferencePayload) &&
    isNullableString(value.faq_question) &&
    isNullableString(value.faq_answer_summary) &&
    (value.reading_minutes === undefined || typeof value.reading_minutes === 'number')
  )
}

function isContentDetailPayload(value: unknown): value is ContentDetail {
  return isRecord(value) && isContentSummaryPayload(value) && typeof value.body === 'string'
}

export async function fetchContents(slug: string, limit?: number): Promise<ContentSummary[]> {
  const base = `${getApiBase()}/hospitals/${encodeURIComponent(slug)}/contents`
  const url = limit ? `${base}?limit=${limit}` : base
  const res = await fetch(url, publicFetchInit(1800))
  if (!res.ok) return []
  const contents = await res.json()
  if (!Array.isArray(contents) || !contents.every(isContentSummaryPayload)) {
    throw new Error('Invalid contents payload')
  }
  return contents
}

export async function fetchContent(slug: string, contentId: string): Promise<ContentDetail> {
  const res = await fetch(
    `${getApiBase()}/hospitals/${encodeURIComponent(slug)}/contents/${encodeURIComponent(contentId)}`,
    publicFetchInit(1800),
  )
  if (res.status === 404) throw new ContentNotFoundError(contentId)
  if (!res.ok) throw new Error(`Server error (${res.status}) when fetching content`)
  const content = await res.json()
  if (!isContentDetailPayload(content)) {
    throw new Error('Invalid content payload')
  }
  return content
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
