/**
 * GA4 계측 규칙 (작업지시서 REP-001·REP-002).
 *
 * 측정 ID는 `motionlabs.kr`과 **같은 속성**을 쓴다. 별도 속성을 만들면 등록 도메인이
 * 같은 두 사이트가 서로 referral로 잡혀 유입 경로가 오염되고, 이미 만들어 둔 탐색
 * 보고서가 Re:putation 유입을 집계하지 못한다.
 *
 * 이 파일이 host를 따지는 이유는 **이 레이아웃이 병원 커스텀 도메인도 함께 서빙하기
 * 때문이다.** 남의 도메인에 `.motionlabs.kr` 쿠키를 쓰려 하면 브라우저가 거부해 쿠키가
 * 아예 저장되지 않는다. 그래서 motionlabs 계열에서만 등록 도메인 쿠키를 쓴다.
 */

/** motionlabs.kr과 공유하는 GA4 측정 ID. 환경변수가 있으면 그것이 우선한다. */
export const DEFAULT_GA_MEASUREMENT_ID = 'G-K8DQWCD39Y'

const MEASUREMENT_ID_PATTERN = /^G-[A-Z0-9]+$/

export type GaPlan = {
  measurementId: string
  /** gtag `cookie_domain` — 'auto'는 현재 호스트에 그대로 심는다. */
  cookieDomain: string
}

function isMotionlabsHost(hostname: string): boolean {
  const host = (hostname || '').toLowerCase()
  return host === 'motionlabs.kr' || host.endsWith('.motionlabs.kr')
}

/**
 * 이 호스트에서 GA4를 켤지, 켠다면 어떤 쿠키 도메인을 쓸지.
 *
 * - motionlabs 계열: 기본 측정 ID로 켜고 `.motionlabs.kr` 쿠키를 쓴다(세션 연결).
 * - 그 외(병원 커스텀 도메인·localhost): **운영자가 환경변수로 명시했을 때만** 켠다.
 *   기본값으로 켜 버리면 병원 자체 도메인 방문자가 묻지도 않고 우리 속성에 쌓인다.
 */
export function resolveGaPlan(hostname: string, configuredId: string | undefined): GaPlan | null {
  const configured = configuredId?.trim()
  if (configured && !MEASUREMENT_ID_PATTERN.test(configured)) return null

  if (isMotionlabsHost(hostname)) {
    return {
      measurementId: configured || DEFAULT_GA_MEASUREMENT_ID,
      cookieDomain: '.motionlabs.kr',
    }
  }
  if (configured) {
    return { measurementId: configured, cookieDomain: 'auto' }
  }
  return null
}

type GtagValue = string | number | boolean
type Gtag = (...args: unknown[]) => void

function getGtag(): Gtag | null {
  if (typeof window === 'undefined') return null
  const candidate = (window as unknown as { gtag?: unknown }).gtag
  return typeof candidate === 'function' ? (candidate as Gtag) : null
}

/**
 * 모든 이벤트에 붙는 공통 파라미터. `service`로 리비짓 리드와 Re:putation 리드를
 * 같은 속성 안에서 갈라 본다.
 */
export function buildEventParams(
  params: Record<string, GtagValue | undefined>,
): Record<string, GtagValue> {
  const out: Record<string, GtagValue> = { service: 'reputation' }
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== '') out[key] = value
  }
  return out
}

/**
 * gtag가 준비되기 전에 발생한 이벤트를 담아 두는 큐.
 *
 * 폼 컴포넌트의 이펙트는 GA 스크립트(afterInteractive)보다 **먼저** 돈다. 그래서
 * `lead_form_view`를 곧바로 쏘면 gtag가 아직 없어 조용히 사라진다 — 하필 가장 많이
 * 발생하는 이벤트가. dataLayer에 직접 밀어 넣는 방법도 있지만, `config`보다 앞선
 * 이벤트는 GA4가 버린다. 그래서 **순서를 지켜 config 직후에 흘려보낸다.**
 */
const pending: Array<{ name: string; params: Record<string, GtagValue> }> = []
/** 계측이 꺼진 호스트에서 큐가 무한정 자라지 않게 한다. 정상 세션은 5개를 넘지 않는다. */
const PENDING_MAX = 20
let configured = false

function send(name: string, params: Record<string, GtagValue>): void {
  const gtag = getGtag()
  if (!gtag) return
  try {
    gtag('event', name, params)
  } catch {
    // 계측 실패는 사용자 흐름을 막지 않는다.
  }
}

/**
 * dataLayer·gtag 스텁을 만들고 `js`·`config`를 보낸 뒤, 밀린 이벤트를 흘려보낸다.
 *
 * 인라인 `<Script>`의 `onReady`에 기대지 않는 이유는 **그 콜백이 돌지 않는 경우가 있기
 * 때문이다**(실측: 인라인 스크립트는 실행됐는데 onReady 미발화 → 큐가 영원히 안 비워짐).
 * 대신 표준 gtag 스니펫과 같은 계약을 쓴다: 스텁이 호출을 dataLayer에 쌓아 두고,
 * 원격 gtag.js가 로드되면 **쌓인 순서 그대로** 처리한다. 그래서 여기서 동기로 부르면
 * `js → config → event` 순서가 보장된다. config보다 앞선 이벤트는 GA4가 버린다.
 */
export function configureGa(plan: GaPlan): void {
  if (typeof window === 'undefined' || configured) return
  const target = window as unknown as { dataLayer?: unknown[]; gtag?: Gtag }
  target.dataLayer = target.dataLayer || []
  if (typeof target.gtag !== 'function') {
    target.gtag = function gtag(...args: unknown[]) {
      // gtag.js는 arguments 객체를 기대한다 — 배열을 넣으면 처리되지 않는다.
      target.dataLayer?.push(arguments)
    } as Gtag
  }
  const gtag = target.gtag
  if (!gtag) return
  gtag('js', new Date())
  gtag('config', plan.measurementId, {
    cookie_domain: plan.cookieDomain,
    cookie_flags: 'SameSite=None;Secure',
  })
  markGaConfigured()
}

/** config가 나간 뒤의 상태 전환 — 밀린 이벤트를 순서대로 흘려보낸다. */
export function markGaConfigured(): void {
  configured = true
  for (const event of pending.splice(0, pending.length)) {
    send(event.name, event.params)
  }
}

/**
 * GA4 이벤트 발화. gtag가 없으면(차단·미설정·개발 환경) 조용히 넘어간다 —
 * 계측이 폼을 망가뜨리는 일은 없어야 한다.
 */
export function trackEvent(
  name: string,
  params: Record<string, GtagValue | undefined> = {},
): void {
  const built = buildEventParams(params)
  if (!configured) {
    if (pending.length < PENDING_MAX) pending.push({ name, params: built })
    return
  }
  send(name, built)
}

/** 테스트용 — 모듈 상태를 초기화한다. */
export function resetAnalyticsStateForTest(): void {
  configured = false
  pending.length = 0
}

/** 테스트용 — 아직 흘려보내지 않은 이벤트. */
export function pendingEventsForTest(): ReadonlyArray<{ name: string; params: Record<string, GtagValue> }> {
  return pending
}

/**
 * SPA 화면 전환의 page_view.
 *
 * `gtag('config')`는 최초 1회만 page_view를 보낸다. 랜딩에서 `/ai-diagnosis`로 가는
 * 건 Next의 클라이언트 이동이라 **그대로 두면 전환 페이지가 보고서에 아예 뜨지 않는다.**
 */
export function trackPageView(path: string): void {
  trackEvent('page_view', {
    page_path: path,
    page_location: typeof window !== 'undefined' ? window.location.href : undefined,
    page_title: typeof document !== 'undefined' ? document.title : undefined,
  })
}
