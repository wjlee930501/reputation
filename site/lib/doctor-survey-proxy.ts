import { getApiBase } from './config.ts'
import { buildLeadOutboundHeaders } from './leads-proxy.ts'
import { BodyTooLargeError, readJsonBodyWithLimit } from './request-body.ts'

/**
 * 원장 사전 설문 프록시 — 브라우저는 같은 출처(`/api/doctor-survey/…`)만 부르고 서버가 백엔드로 넘긴다.
 *
 * 개인에게 발급된 링크라 응답에 no-store·noindex·no-referrer를 붙인다(CDN이 답을 저장하지 않게).
 * 방문자 IP와 BFF 비밀값은 도입문의 프록시와 같은 방식으로 실어 백엔드 레이트 리밋이 방문자 기준이 되게 한다.
 */

const GUARD = {
  'Cache-Control': 'no-store',
  'X-Robots-Tag': 'noindex, nofollow',
  'Referrer-Policy': 'no-referrer',
} as const

const MAX_BODY_BYTES = 300 * 1024
const TOKEN_PATTERN = /^[A-Za-z0-9_-]{20,100}$/

/** 허용하는 (메서드, 동작) 쌍. 그 밖은 404다. */
const ROUTES: Record<string, string | null> = {
  'GET:': '',
  'POST:consent': '/consent',
  'PUT:answers': '/answers',
  'POST:submit': '/submit',
}

export function surveyUpstreamPath(method: string, token: string, action: string[] | undefined): string | null {
  if (!TOKEN_PATTERN.test(token)) return null
  const name = (action ?? []).join('/')
  const suffix = ROUTES[`${method}:${name}`]
  if (suffix === undefined || suffix === null) return null
  return `/doctor-survey/${encodeURIComponent(token)}${suffix}`
}

function json(payload: unknown, status: number): Response {
  return Response.json(payload, { status, headers: GUARD })
}

export async function proxySurveyRequest(
  request: Request,
  params: Promise<{ token: string; action?: string[] }>,
): Promise<Response> {
  const { token, action } = await params
  const path = surveyUpstreamPath(request.method, token, action)
  if (!path) return json({ detail: '유효하지 않은 링크입니다.' }, 404)

  let body: string | undefined
  if (request.method !== 'GET') {
    try {
      body = JSON.stringify(await readJsonBodyWithLimit(request, MAX_BODY_BYTES))
    } catch (error) {
      if (error instanceof BodyTooLargeError) {
        return json({ detail: '답이 너무 깁니다. 긴 답을 줄여 주세요.' }, 413)
      }
      return json({ detail: '입력 형식을 확인할 수 없습니다. 화면을 새로고침해 주세요.' }, 400)
    }
  }

  let upstream: Response
  try {
    upstream = await fetch(`${getApiBase()}${path}`, {
      method: request.method,
      headers: buildLeadOutboundHeaders(request.headers),
      body,
      cache: 'no-store',
    })
  } catch {
    return json({ detail: '잠시 연결이 원활하지 않습니다. 잠시 뒤 다시 시도해 주세요.' }, 502)
  }
  const payload = await upstream.json().catch(() => ({}))
  return json(payload, upstream.status)
}
