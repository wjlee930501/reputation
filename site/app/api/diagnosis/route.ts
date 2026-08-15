import { NextResponse } from 'next/server'
import { getApiBase } from '@/lib/config'
import { containsPatientSensitiveLeadText, leadSafetyError } from '@/lib/lead-safety'
import { buildLeadOutboundHeaders, isLeadValidationUpstreamStatus } from '@/lib/leads-proxy'
import { BodyTooLargeError, readJsonBodyWithLimit } from '@/lib/request-body'

export const runtime = 'nodejs'

/**
 * 무료 진단 접수 BFF.
 *
 * 브라우저가 백엔드를 직접 부르지 않는 이유는 방문자 IP 전달(X-BFF-Auth) 때문이다 —
 * 그 IP가 rate-limit과 동의 기록(consent_ip)의 근거이므로, 프록시를 거쳐야 백엔드가
 * 실제 방문자를 본다.
 *
 * 여기서 하는 검증은 **백엔드 검증의 사본이 아니라 앞단 차단**이다: 환자 민감정보가
 * 담긴 요청은 국외(Slack/백엔드 로그)로 나가기 전에 여기서 끊는다.
 */

const MAX_BODY_BYTES = 32 * 1024 // 정상 폼은 ~1KB. 그 이상이면 abuse.

// 자유 텍스트 필드 — 환자 민감정보 검사 대상.
const FREE_TEXT_FIELDS = ['clinic_name', 'clinic_type', 'region_keyword', 'contact_name'] as const

export async function POST(request: Request) {
  let body: Record<string, unknown>
  try {
    const payload = await readJsonBodyWithLimit(request, MAX_BODY_BYTES)
    body = typeof payload === 'object' && payload !== null && !Array.isArray(payload)
      ? payload as Record<string, unknown>
      : {}
  } catch (error) {
    if (error instanceof BodyTooLargeError) {
      return NextResponse.json({ ok: false, error: '요청이 너무 큽니다.' }, { status: 413 })
    }
    return NextResponse.json({ ok: false, error: '요청 형식이 올바르지 않습니다.' }, { status: 400 })
  }

  for (const field of FREE_TEXT_FIELDS) {
    const value = body[field]
    if (typeof value === 'string' && containsPatientSensitiveLeadText(value)) {
      return NextResponse.json({ ok: false, error: leadSafetyError() }, { status: 400 })
    }
  }
  const keywords = body.core_keywords
  if (Array.isArray(keywords)) {
    for (const keyword of keywords) {
      if (typeof keyword === 'string' && containsPatientSensitiveLeadText(keyword)) {
        return NextResponse.json({ ok: false, error: leadSafetyError() }, { status: 400 })
      }
    }
  }

  let upstream: Response
  try {
    upstream = await fetch(`${getApiBase()}/diagnosis`, {
      method: 'POST',
      headers: buildLeadOutboundHeaders(request.headers),
      body: JSON.stringify(body),
      cache: 'no-store',
    })
  } catch {
    return NextResponse.json(
      { ok: false, error: '접수 서버에 연결하지 못했습니다. 잠시 후 다시 시도해 주세요.' },
      { status: 502 },
    )
  }

  const payload = await upstream.json().catch(() => ({}) as Record<string, unknown>)

  if (upstream.ok) {
    return NextResponse.json(payload, {
      status: 200,
      // 접수 응답에는 그 사람의 상태 페이지 주소가 들어 있다 — 절대 캐시되면 안 된다.
      headers: { 'Cache-Control': 'no-store' },
    })
  }

  // 마감(429)·중복 신청(409)·검증 실패(4xx)는 백엔드 문구를 그대로 보여준다.
  // 우리가 다시 쓰면 "왜 거절됐는지"가 두 곳에서 갈라진다.
  const detail =
    typeof payload?.detail === 'string'
      ? payload.detail
      : isLeadValidationUpstreamStatus(upstream.status)
        ? '입력값을 다시 확인해 주세요.'
        : '접수에 실패했습니다. 잠시 후 다시 시도해 주세요.'

  const status = upstream.status === 409 || upstream.status === 429 ? upstream.status : 400
  return NextResponse.json({ ok: false, error: detail }, { status, headers: { 'Cache-Control': 'no-store' } })
}
