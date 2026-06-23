const BASE = '/api/admin'

// --- Autofill types ---

export interface AutofillFieldMeta {
  source: string
  confidence: number
}

export interface AutofillViolation {
  field: string
  expressions: string[]
}

export interface AutofillSource {
  name: string
  ok: boolean
  reason: string | null
}

export interface AutofillResponse {
  draft: Record<string, unknown>
  field_meta: Record<string, AutofillFieldMeta>
  violations: AutofillViolation[]
  naver_place_id: string | null
  sources: AutofillSource[]
}

export interface AutofillRequest {
  name?: string
  website_url?: string
  blog_url?: string
}

function normalizePath(path: string): string {
  if (path.startsWith('/api/admin/')) return path.slice('/api/admin'.length)
  if (path.startsWith('/admin/')) return path.slice('/admin'.length)
  return path.startsWith('/') ? path : `/${path}`
}

/**
 * 백엔드 API 오류. message는 AE에게 보여줄 수 있는 읽기 좋은 한국어 문장이고,
 * detail에는 원본 구조화 응답(detail 필드)이 그대로 들어 있어
 * 화면별로 violations 등 세부 항목을 활용할 수 있다.
 */
export class ApiError extends Error {
  status: number
  detail: unknown

  constructor(message: string, status: number, detail: unknown = null) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

export async function fetchAPI<T = unknown>(path: string, options?: RequestInit): Promise<T> {
  const { headers: customHeaders, ...rest } = options ?? {}
  // FormData 본문은 브라우저가 boundary 포함 Content-Type을 직접 설정해야 하므로
  // JSON 헤더를 붙이지 않는다.
  const isFormData = typeof FormData !== 'undefined' && rest.body instanceof FormData
  const res = await fetch(`${BASE}${normalizePath(path)}`, {
    ...rest,
    headers: {
      ...(isFormData ? {} : { 'Content-Type': 'application/json' }),
      ...customHeaders,
    },
  })

  if (!res.ok) {
    if (res.status === 401) {
      const currentPath = window.location.pathname + window.location.search
      window.location.href = `/login?redirect=${encodeURIComponent(currentPath)}`
      throw new ApiError('인증이 만료되었습니다. 로그인 페이지로 이동합니다.', 401)
    }
    const body = await res.text()
    const { message, detail } = readError(body, res.status)
    throw new ApiError(message, res.status, detail)
  }

  if (res.status === 204) return null as T
  const text = await res.text()
  return (text ? JSON.parse(text) : null) as T
}

function statusMessage(status: number): string | null {
  const messages: Record<number, string> = {
    400: '입력값이 올바르지 않습니다.',
    401: '인증이 필요합니다. 다시 로그인해 주세요.',
    403: '접근 권한이 없습니다.',
    404: '요청한 정보를 찾을 수 없습니다.',
    409: '이미 처리 중이거나 충돌이 발생했습니다.',
    422: '입력값 검증에 실패했습니다.',
    429: '요청이 너무 많습니다. 잠시 후 다시 시도해 주세요.',
    500: '서버 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.',
    502: '서버가 일시적으로 응답하지 않습니다.',
    503: '서버 점검 중입니다. 잠시 후 다시 시도해 주세요.',
  }
  return messages[status] || null
}

function fallbackMessage(status: number): string {
  return statusMessage(status) ?? `서버 오류 (${status})`
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function toStringList(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  return value.map((entry) => formatDetailEntry(entry)).filter(Boolean)
}

/** FastAPI validation 항목({loc, msg}) 또는 임의 항목을 읽기 좋은 문장으로 변환. */
function formatDetailEntry(entry: unknown): string {
  if (typeof entry === 'string') return entry
  if (isRecord(entry)) {
    const msg = typeof entry.msg === 'string' ? entry.msg : typeof entry.message === 'string' ? entry.message : null
    if (msg) {
      const loc = Array.isArray(entry.loc)
        ? entry.loc.filter((part) => typeof part === 'string' && part !== 'body').join('.')
        : ''
      return loc ? `${loc}: ${msg}` : msg
    }
    if (typeof entry.claim === 'string') return entry.claim
    if (typeof entry.title === 'string') return entry.title
  }
  return typeof entry === 'number' || typeof entry === 'boolean' ? String(entry) : ''
}

/** 알 수 없는 구조의 detail을 진단용으로 덧붙일 때 쓰는 압축 JSON (최대 200자). */
function compactJson(value: unknown): string | null {
  let raw: string | undefined
  try {
    raw = JSON.stringify(value)
  } catch {
    return null
  }
  if (!raw || raw === '{}' || raw === 'null') return null
  return raw.length > 200 ? `${raw.slice(0, 200)}…` : raw
}

/**
 * 구조화된 detail 객체를 AE가 읽을 수 있는 한국어 메시지로 변환한다.
 * 알려진 형태(grounding_errors, violations, FastAPI validation 배열)를 우선 처리하고,
 * 모르는 형태는 상태 코드 기반 일반 메시지 뒤에 압축 JSON을 덧붙여 원인을 숨기지 않는다.
 */
function readError(body: string, status: number): { message: string; detail: unknown } {
  if (!body) return { message: fallbackMessage(status), detail: null }

  let parsed: unknown
  try {
    parsed = JSON.parse(body)
  } catch {
    // JSON이 아니면 텍스트 그대로 노출 (백엔드가 평문을 보낸 경우)
    return { message: body, detail: null }
  }

  if (!isRecord(parsed) || !('detail' in parsed)) {
    // detail은 없지만 error/message 문자열이 있으면 그대로 노출 —
    // admin 프록시 자체 오류({"error":"Server misconfigured"}) 등이 여기에 해당한다.
    if (isRecord(parsed)) {
      const direct =
        typeof parsed.error === 'string' && parsed.error
          ? parsed.error
          : typeof parsed.message === 'string' && parsed.message
            ? parsed.message
            : null
      if (direct) return { message: direct, detail: parsed }
    }
    return { message: fallbackMessage(status), detail: parsed ?? null }
  }

  const detail = (parsed as { detail?: unknown }).detail
  if (typeof detail === 'string') return { message: detail, detail }

  // FastAPI validation 배열: [{loc, msg, type}, ...]
  if (Array.isArray(detail)) {
    const lines = toStringList(detail)
    return {
      message: lines.length > 0 ? lines.join('\n') : fallbackMessage(status),
      detail,
    }
  }

  if (isRecord(detail)) {
    // 운영 기준 grounding 오류: {grounding_errors: [...]}
    if (Array.isArray(detail.grounding_errors)) {
      const lines = toStringList(detail.grounding_errors)
      const head = '근거 검증에 실패한 항목이 있습니다.'
      return {
        message: lines.length > 0 ? `${head}\n- ${lines.join('\n- ')}` : head,
        detail,
      }
    }
    // 의료광고/발행 차단: {message, violations?} 또는 {message, missing?}
    if (typeof detail.message === 'string') {
      const violations = toStringList(detail.violations)
      return {
        message: violations.length > 0 ? `${detail.message} (${violations.join(', ')})` : detail.message,
        detail,
      }
    }

    // 모르는 객체 형태 — 일반 메시지로 뭉개면 원인이 보이지 않으므로 압축 JSON을 덧붙인다.
    const raw = compactJson(detail)
    return {
      message: raw ? `${fallbackMessage(status)} (상세: ${raw})` : fallbackMessage(status),
      detail,
    }
  }

  return { message: fallbackMessage(status), detail: detail ?? null }
}

export async function autofillProfile(
  hospitalId: string,
  body: AutofillRequest,
): Promise<AutofillResponse> {
  return fetchAPI<AutofillResponse>(
    `/admin/hospitals/${hospitalId}/profile/autofill`,
    {
      method: 'POST',
      body: JSON.stringify(body),
      // Allow up to 60s for the slow scraping call
      signal: AbortSignal.timeout(60_000),
    },
  )
}
