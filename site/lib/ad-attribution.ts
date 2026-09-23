/**
 * 광고 유입 식별자(UTM·OpenAI 파라미터)의 캡처·보존·전달 규칙 (작업지시서 REP-003).
 *
 * 광고 클릭은 `/?utm_source=chatgpt&...&oppref=<자동부착>` 형태로 들어온다. 이 값이
 * 살아남아야 하는 지점은 두 곳이다.
 *
 *   1. GA4 — 세션 소스는 gtag가 알아서 잡는다. 우리가 할 일은 없다.
 *   2. **리드 레코드** — 리포트를 보내는 사람이 "이 리드가 광고 유입인지" 알아야 한다.
 *      GA4는 개인 리드와 연결되지 않으므로 여기는 우리가 직접 실어 보내야 한다.
 *
 * 그런데 랜딩(`/`)과 신청 폼(`/ai-diagnosis`)은 다른 페이지다. 클릭한 주소의 쿼리는
 * 폼에 도달하기 전에 사라진다. 그래서 **최초 진입에서 캡처해 쿠키에 담아 두고**
 * 제출 시점에 꺼내 쓴다. 30일을 두는 것은 "오늘 보고 내일 신청하는" 원장이 실재하기
 * 때문이다 — 그 리드도 광고가 만든 리드다.
 *
 * 백엔드는 `source_path` 하나만 받는다(SalesLead.source_path, 500자). 광고 식별자용
 * 컬럼이 생기기 전까지는 **그 한 칸에 쿼리스트링으로 실어 보낸다.** 관리자 화면이 이미
 * source_path를 그대로 노출하므로, 백엔드 변경 없이 오늘부터 광고 리드를 구분할 수 있다.
 */

/** 보존 대상 파라미터. 앞쪽일수록 우선 — 500자 한도에서 뒤부터 빠진다. */
export const ATTRIBUTION_KEYS = [
  'utm_source',
  'utm_medium',
  'utm_campaign',
  'oppref',
  'utm_content',
  'utm_term',
  'oai_campaign',
  'oai_adgroup',
] as const

export type AttributionKey = (typeof ATTRIBUTION_KEYS)[number]

export type Attribution = Partial<Record<AttributionKey, string>> & {
  /** 광고를 클릭해 처음 도착한 경로. 어느 랜딩이 리드를 만들었는지의 근거. */
  landing_path: string
}

export const ATTRIBUTION_COOKIE = 'reputation_ad_attribution'
/** 30일 — 지시서 REP-003 4항. */
export const ATTRIBUTION_MAX_AGE_SECONDS = 30 * 24 * 60 * 60

/** 값 하나의 상한. 광고 식별자는 길어야 수십 자다. 그 이상은 주입 시도로 본다. */
const VALUE_MAX = 200
/** 백엔드 SalesLead.source_path 컬럼 길이. */
const SOURCE_PATH_MAX = 500

/** 제어문자는 쿠키·헤더·로그를 깨뜨린다. 값으로 쓸 수 있는 형태만 남긴다. */
const CONTROL_CHARS = /[\x00-\x1f\x7f]/g

function sanitizeValue(raw: string | null): string | undefined {
  if (raw === null) return undefined
  const cleaned = raw.replace(CONTROL_CHARS, '').trim()
  if (!cleaned) return undefined
  return cleaned.slice(0, VALUE_MAX)
}

function sanitizePath(raw: string): string {
  const cleaned = (raw || '/').replace(CONTROL_CHARS, '').trim()
  if (!cleaned.startsWith('/')) return '/'
  return cleaned.slice(0, VALUE_MAX)
}

/**
 * 현재 URL에서 광고 식별자를 읽는다. 광고 파라미터가 하나도 없으면 null —
 * 자연 유입까지 캡처하면 저장해 둔 광고 유입을 덮어써 버린다.
 */
export function parseAttribution(search: string, pathname: string): Attribution | null {
  const params = new URLSearchParams(search.startsWith('?') ? search.slice(1) : search)
  const found: Partial<Record<AttributionKey, string>> = {}
  for (const key of ATTRIBUTION_KEYS) {
    const value = sanitizeValue(params.get(key))
    if (value) found[key] = value
  }
  if (Object.keys(found).length === 0) return null
  return { ...found, landing_path: sanitizePath(pathname) }
}

export function serializeAttribution(attribution: Attribution): string {
  return encodeURIComponent(JSON.stringify(attribution))
}

export function deserializeAttribution(raw: string): Attribution | null {
  if (!raw) return null
  let parsed: unknown
  try {
    parsed = JSON.parse(decodeURIComponent(raw))
  } catch {
    return null
  }
  if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) return null
  const record = parsed as Record<string, unknown>
  const found: Partial<Record<AttributionKey, string>> = {}
  for (const key of ATTRIBUTION_KEYS) {
    const value = record[key]
    if (typeof value === 'string') {
      const cleaned = sanitizeValue(value)
      if (cleaned) found[key] = cleaned
    }
  }
  if (Object.keys(found).length === 0) return null
  const landingPath = typeof record.landing_path === 'string' ? record.landing_path : '/'
  return { ...found, landing_path: sanitizePath(landingPath) }
}

/** `document.cookie` 문자열에서 저장된 캡처값을 꺼낸다. */
export function readAttributionCookie(cookieString: string): Attribution | null {
  for (const piece of (cookieString || '').split(';')) {
    const separator = piece.indexOf('=')
    if (separator < 0) continue
    if (piece.slice(0, separator).trim() !== ATTRIBUTION_COOKIE) continue
    return deserializeAttribution(piece.slice(separator + 1).trim())
  }
  return null
}

/**
 * 쿠키를 심을 도메인.
 *
 * `motionlabs.kr` 계열에서는 등록 도메인에 심어 `motionlabs.kr` → `reputation.motionlabs.kr`
 * 이동에서도 캡처값이 따라가게 한다. 병원 커스텀 도메인에서는 **절대 쓰면 안 된다** —
 * 브라우저가 자기 도메인이 아닌 쿠키를 거부해 아예 저장되지 않는다.
 */
export function attributionCookieDomain(hostname: string): string | null {
  const host = (hostname || '').toLowerCase()
  return host === 'motionlabs.kr' || host.endsWith('.motionlabs.kr') ? '.motionlabs.kr' : null
}

export function buildAttributionCookie(attribution: Attribution, hostname: string): string {
  const domain = attributionCookieDomain(hostname)
  // Secure는 https에서만 유효하다. 로컬(http) 개발에서 붙이면 쿠키가 저장되지 않는다.
  const secure = hostname !== 'localhost' && hostname !== '127.0.0.1'
  return [
    `${ATTRIBUTION_COOKIE}=${serializeAttribution(attribution)}`,
    'path=/',
    `max-age=${ATTRIBUTION_MAX_AGE_SECONDS}`,
    'SameSite=Lax',
    ...(domain ? [`domain=${domain}`] : []),
    ...(secure ? ['Secure'] : []),
  ].join('; ')
}

/**
 * 폼이 보낼 `source_path`에 캡처값을 실어 준다.
 *
 * 백엔드에 광고 식별자 컬럼이 생기기 전까지의 운반 수단이다. 500자를 넘기면 백엔드가
 * 리드 자체를 거절하므로 **한도를 넘는 파라미터는 싣지 않는다** — 광고 정보가 조금
 * 빠지는 것보다 리드를 잃는 쪽이 훨씬 비싸다.
 */
export function decorateSourcePath(sourcePath: string, attribution: Attribution | null): string {
  if (!attribution) return sourcePath
  const params = new URLSearchParams()
  const append = (key: string, value: string): void => {
    const candidate = new URLSearchParams(params)
    candidate.append(key, value)
    if (`${sourcePath}?${candidate}`.length > SOURCE_PATH_MAX) return
    params.append(key, value)
  }
  for (const key of ATTRIBUTION_KEYS) {
    const value = attribution[key]
    if (value) append(key, value)
  }
  append('landing_path', attribution.landing_path)
  const query = params.toString()
  return query ? `${sourcePath}?${query}` : sourcePath
}

/** GA4 이벤트에 붙일 파라미터 — 리드 이벤트를 캠페인별로 쪼개 볼 수 있게 한다. */
export function attributionEventParams(attribution: Attribution | null): Record<string, string> {
  if (!attribution) return {}
  const params: Record<string, string> = {}
  for (const key of ATTRIBUTION_KEYS) {
    const value = attribution[key]
    if (value) params[key] = value
  }
  params.landing_path = attribution.landing_path
  return params
}
