/**
 * OpenAI 광고 전환 픽셀(Measurement Pixel) — 작업지시서 PIX-001·002·003·007.
 *
 * 왜 필요한가: GA4는 **우리 쪽 분석 도구일 뿐**이고, OpenAI 입찰 알고리즘은 자사 픽셀로
 * 들어온 전환만 학습한다. 전환 신호가 0인 상태의 자동 입찰은 "무엇이 좋은 클릭인지"
 * 모르는 채 클릭 수만 최대화하므로, 1초 만에 이탈하는 클릭을 ₩4,000씩 사게 된다
 * (2026-09-22 리비짓 집행 실측: 클릭 12 → 세션 3 → 주요 이벤트 0).
 *
 * `lib/analytics.ts`(GA4)와 구조가 닮았지만 **합치지 않는다.** 두 계측은 소유자도,
 * 이벤트 이름 규약도, 켜고 끄는 조건도 다르다. 한쪽 규약이 바뀔 때 다른 쪽이 딸려
 * 움직이는 결합이 이득보다 비싸다.
 */

/** Ads Manager에서 발급한 데이터 소스 ID (모션랩스 웹사이트, 2026-09-22). */
export const OPENAI_PIXEL_ID = 'P1pvoNUixGrftiYAefioGV'

export const OPENAI_PIXEL_SDK_URL = 'https://bzrcdn.openai.com/sdk/oaiq.min.js'

const PIXEL_ID_PATTERN = /^[A-Za-z0-9]{10,64}$/

function isMotionlabsHost(hostname: string): boolean {
  const host = (hostname || '').toLowerCase()
  return host === 'motionlabs.kr' || host.endsWith('.motionlabs.kr')
}

/**
 * 이 호스트에서 픽셀을 켤지.
 *
 * GA4와 같은 이유로 **병원 커스텀 도메인에서는 켜지 않는다.** 그 방문자는 우리 광고의
 * 전환 후보가 아니고, 남의 도메인 방문 기록을 우리 광고 계정으로 보낼 근거도 없다.
 * 운영자가 환경변수로 명시하면(스테이징 검증 등) 그때만 켠다.
 */
export function resolvePixelId(hostname: string, configuredId: string | undefined): string | null {
  const configured = configuredId?.trim()
  if (configured && !PIXEL_ID_PATTERN.test(configured)) return null
  if (isMotionlabsHost(hostname)) return configured || OPENAI_PIXEL_ID
  return configured || null
}

type PixelParams = Record<string, unknown>
type Oaiq = (...args: unknown[]) => void

/**
 * `page_viewed`에 실을 콘텐츠 서술 (PIX-003).
 *
 * 전환 이벤트만 있으면 "광고 클릭 → 랜딩 도달"과 "랜딩 도달 → 신청" 중 **어디서 새는지**
 * 구분할 수 없다. 지시서가 정한 두 지면만 보내고, `contents[]`에는 문서에 정의된 필드만
 * 넣는다(임의 필드 금지).
 */
export function pageViewedContent(
  pathname: string,
): { id: string; name: string; content_type: 'page' } | null {
  const path = (pathname || '').replace(/\/+$/, '') || '/'
  if (path === '/') {
    return { id: 'reputation_landing', name: 'Re:putation 랜딩', content_type: 'page' }
  }
  if (path === '/ai-diagnosis') {
    return { id: 'ai_diagnosis_form', name: 'AI 노출 진단 신청', content_type: 'page' }
  }
  return null
}

/** 접수 레코드 ID를 이벤트 ID로 만든다 — 서버 중복 전송이 붙어도 한 번만 집계된다(PIX-007). */
export function leadEventId(diagnosisId: string): string {
  return `diagnosis_${diagnosisId}`
}

function getOaiq(): Oaiq | null {
  if (typeof window === 'undefined') return null
  const candidate = (window as unknown as { oaiq?: unknown }).oaiq
  return typeof candidate === 'function' ? (candidate as Oaiq) : null
}

/**
 * init 전에 발생한 이벤트를 담아 두는 큐.
 *
 * SDK 스텁도 자체 큐를 갖지만 그것만으로는 부족하다 — **`init`보다 앞선 `measure`는
 * 소속 픽셀이 없다.** 폼·페이지 이펙트는 레이아웃의 init보다 먼저 돌 수 있으므로,
 * 순서를 여기서 보장한다(GA4에서 `lead_form_view`가 통째로 사라졌던 것과 같은 함정).
 */
type PendingEvent = { name: string; params: PixelParams; options?: PixelParams }
const pending: PendingEvent[] = []
/** 픽셀이 꺼진 호스트에서 큐가 무한정 자라지 않게 한다. 정상 세션은 3개를 넘지 않는다. */
const PENDING_MAX = 20
let initialized = false

function send(event: PendingEvent): void {
  const oaiq = getOaiq()
  if (!oaiq) return
  try {
    if (event.options) {
      oaiq('measure', event.name, event.params, event.options)
    } else {
      oaiq('measure', event.name, event.params)
    }
  } catch {
    // 계측 실패가 신청 흐름을 막는 일은 없어야 한다.
  }
}

/**
 * 스텁을 만들고 `init`을 보낸 뒤 밀린 이벤트를 흘려보낸다.
 *
 * 원격 SDK를 기다리지 않는다. 스텁이 호출을 자체 큐에 쌓아 두고 SDK가 로드되면
 * 쌓인 순서대로 처리하므로, 여기서 동기로 부르면 `init → measure` 순서가 보장된다.
 * `debug: true`는 **절대 넣지 않는다** — Ads Manager 복사용 스니펫에 포함돼 있어
 * 그대로 운영에 올라가기 쉬운 값이다.
 */
export function initPixel(pixelId: string): void {
  if (typeof window === 'undefined' || initialized) return
  const target = window as unknown as { oaiq?: Oaiq & { q?: unknown[] } }
  if (typeof target.oaiq !== 'function') {
    const stub = function oaiq() {
      // SDK가 arguments 객체를 기대한다 — 배열로 바꾸지 않는다.
      stub.q.push(arguments)
    } as Oaiq & { q: unknown[] }
    stub.q = []
    target.oaiq = stub
  }
  const oaiq = target.oaiq
  if (!oaiq) return
  try {
    oaiq('init', { pixelId })
  } catch {
    return
  }
  initialized = true
  for (const event of pending.splice(0, pending.length)) send(event)
}

/** 픽셀 이벤트 발화. 픽셀이 꺼져 있으면 큐에만 쌓이고 아무 요청도 나가지 않는다. */
export function trackPixelEvent(
  name: string,
  params: PixelParams,
  options?: PixelParams,
): void {
  const event: PendingEvent = { name, params, ...(options ? { options } : {}) }
  if (!initialized) {
    if (pending.length < PENDING_MAX) pending.push(event)
    return
  }
  send(event)
}

/** `page_viewed` — 지시서가 정한 두 지면에서만 발화한다. */
export function trackPixelPageViewed(pathname: string): void {
  const content = pageViewedContent(pathname)
  if (!content) return
  trackPixelEvent('page_viewed', { type: 'contents', contents: [content] })
}

/**
 * 전환 — **접수가 서버에서 성공 처리된 뒤에만** 부른다(PIX-002).
 *
 * `diagnosisId`가 없으면 보내지 않는다. 백엔드는 허니팟에 걸린 요청에도 200을 주되
 * `diagnosis_id`를 null로 돌려주므로, 이 값의 유무가 **실제로 저장된 신청인지**의
 * 가장 정확한 신호다. 봇 제출이 전환으로 잡히면 입찰 알고리즘이 그것을 학습한다.
 */
export function trackPixelLeadCreated(diagnosisId: string | null | undefined): void {
  if (!diagnosisId) return
  trackPixelEvent(
    'lead_created',
    { type: 'customer_action' },
    { event_id: leadEventId(diagnosisId) },
  )
}

/** 테스트용 — 모듈 상태를 초기화한다. */
export function resetPixelStateForTest(): void {
  initialized = false
  pending.length = 0
}

/** 테스트용 — 아직 흘려보내지 않은 이벤트. */
export function pendingPixelEventsForTest(): ReadonlyArray<PendingEvent> {
  return pending
}

/** 테스트용 — init이 끝났는지. */
export function markPixelInitializedForTest(): void {
  initialized = true
  pending.splice(0, pending.length)
}
