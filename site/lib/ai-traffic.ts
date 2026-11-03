// AI 크롤러 방문과 AI 답변에서 들어온 방문을 구조화 로그 한 줄로 남긴다.
//
// 왜 필요한가. 발행 → 색인 → 인용 → 언급 중 "색인"은 지금 시스템이 보지 못하는 구간이다.
// 크롤러가 병원 호스트를 실제로 가져가는지(OAI-SearchBot·Googlebot·Bingbot·Yeti …)와
// ChatGPT·Gemini 답변의 링크로 환자가 들어오는지는 그 구간의 선행 지표다.
// Cloud Run이 stdout JSON을 Cloud Logging 구조화 로그로 수집하므로 별도 저장소 없이
// `jsonPayload.event="ai_crawler_hit"`로 호스트별 집계를 낼 수 있다.
//
// User-Agent·Referer는 위조할 수 있으므로 지표일 뿐 인증 근거가 아니다. 경로만 남기고
// 질의 문자열·IP·쿠키는 남기지 않는다(개인정보 최소 수집).

const CRAWLER_PATTERNS: readonly (readonly [string, RegExp])[] = [
  ['OAI-SearchBot', /OAI-SearchBot/i],
  ['ChatGPT-User', /ChatGPT-User/i],
  ['GPTBot', /GPTBot/i],
  ['Google-Extended', /Google-Extended/i],
  ['Googlebot', /Googlebot/i],
  ['Google-InspectionTool', /Google-InspectionTool/i],
  ['Bingbot', /bingbot/i],
  ['PerplexityBot', /PerplexityBot/i],
  ['Perplexity-User', /Perplexity-User/i],
  ['ClaudeBot', /ClaudeBot/i],
  ['Claude-User', /Claude-User/i],
  ['Claude-SearchBot', /Claude-SearchBot/i],
  ['Yeti', /Yeti\//i],
  ['Daumoa', /Daumoa/i],
  ['Applebot', /Applebot/i],
] as const

/** User-Agent에서 알려진 검색·AI 크롤러 이름을 고른다. 모르면 null. */
export function classifyCrawler(userAgent: string | null | undefined): string | null {
  const ua = (userAgent ?? '').trim()
  if (!ua) return null
  for (const [name, pattern] of CRAWLER_PATTERNS) {
    if (pattern.test(ua)) return name
  }
  return null
}

const REFERRER_HOSTS: readonly (readonly [string, (host: string) => boolean])[] = [
  ['chatgpt', (h) => h === 'chatgpt.com' || h.endsWith('.chatgpt.com') || h === 'chat.openai.com'],
  ['gemini', (h) => h === 'gemini.google.com' || h === 'bard.google.com'],
  ['perplexity', (h) => h === 'perplexity.ai' || h.endsWith('.perplexity.ai')],
  ['copilot', (h) => h === 'copilot.microsoft.com'],
  ['claude', (h) => h === 'claude.ai'],
] as const

const UTM_SOURCES: Readonly<Record<string, string>> = {
  'chatgpt.com': 'chatgpt',
  chatgpt: 'chatgpt',
  openai: 'chatgpt',
  'perplexity.ai': 'perplexity',
  perplexity: 'perplexity',
  'gemini.google.com': 'gemini',
  gemini: 'gemini',
  'copilot.microsoft.com': 'copilot',
  copilot: 'copilot',
}

/**
 * 방문이 AI 답변에서 왔는지 고른다. Referer 호스트를 먼저 보고, 없으면 utm_source를 본다.
 * ChatGPT의 `utm_source=chatgpt.com`은 공식 보장이 아니고 누락된 사례도 있어 둘 다 본다.
 */
export function classifyAiReferral(
  referer: string | null | undefined,
  utmSource: string | null | undefined,
): string | null {
  const raw = (referer ?? '').trim()
  if (raw) {
    try {
      const host = new URL(raw).hostname.toLowerCase()
      for (const [name, matches] of REFERRER_HOSTS) {
        if (matches(host)) return name
      }
    } catch {
      // 잘못된 Referer는 무시한다.
    }
  }
  const utm = (utmSource ?? '').trim().toLowerCase()
  return utm ? (UTM_SOURCES[utm] ?? null) : null
}

export interface AiTrafficEvent {
  event: 'ai_crawler_hit' | 'ai_referral_visit'
  severity: 'INFO'
  message: string
  host: string
  path: string
  crawler?: string
  source?: string
}

const MAX_PATH = 200

/** 요청 한 건에서 남길 이벤트를 만든다(순수 함수). 해당 없으면 null. */
export function aiTrafficEvent(input: {
  host: string | null
  pathname: string
  userAgent: string | null
  referer: string | null
  utmSource: string | null
}): AiTrafficEvent | null {
  const host = (input.host ?? '').trim().toLowerCase().split(':', 1)[0]
  if (!host) return null
  const path = input.pathname.slice(0, MAX_PATH) || '/'
  const crawler = classifyCrawler(input.userAgent)
  if (crawler) {
    return { event: 'ai_crawler_hit', severity: 'INFO', message: `ai_crawler_hit ${crawler}`, host, path, crawler }
  }
  const source = classifyAiReferral(input.referer, input.utmSource)
  if (source) {
    return { event: 'ai_referral_visit', severity: 'INFO', message: `ai_referral_visit ${source}`, host, path, source }
  }
  return null
}

/** 로그 한 줄. 어떤 실패도 요청 처리를 막지 않는다. */
export function logAiTraffic(event: AiTrafficEvent | null): void {
  if (!event) return
  try {
    console.log(JSON.stringify(event))
  } catch {
    // 로깅 실패는 무시한다.
  }
}
