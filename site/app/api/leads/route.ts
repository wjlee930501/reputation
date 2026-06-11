import { NextResponse } from 'next/server'
import { clientIpFromForwardedHeaders } from '@/lib/client-ip'
import { getApiBase } from '@/lib/config'

export const runtime = 'nodejs'

const REQUIRED_FIELDS = ['clinicName', 'clinicType', 'contact', 'question'] as const
const FIELD_MAX = {
  clinicName: 200,
  clinicType: 200,
  contact: 200,
  question: 1000,
  consent_version: 40,
} as const
const MAX_BODY_BYTES = 64 * 1024 // 64KB — 정상 폼은 ~1KB. 그 이상이면 abuse.

function readField(formData: FormData, field: string, max: number) {
  const value = formData.get(field)
  if (typeof value !== 'string') return ''
  return value.trim().slice(0, max)
}

export async function POST(request: Request) {
  const wantsJson = request.headers.get('accept')?.includes('application/json') ?? false

  const contentLength = Number(request.headers.get('content-length') || '0')
  if (contentLength && contentLength > MAX_BODY_BYTES) {
    return wantsJson
      ? NextResponse.json({ ok: false, error: 'Payload too large' }, { status: 413 })
      : NextResponse.redirect(new URL('/?lead=invalid#lead', request.url), 303)
  }

  let formData: FormData
  try {
    formData = await request.formData()
  } catch {
    return wantsJson
      ? NextResponse.json({ ok: false, error: 'Invalid form payload' }, { status: 400 })
      : NextResponse.redirect(new URL('/?lead=invalid#lead', request.url), 303)
  }

  // Honeypot — 봇이 자동으로 채우는 필드. 채워져 있으면 silently 200.
  const honey = readField(formData, 'website', 500)
  if (honey) {
    return wantsJson
      ? NextResponse.json({ ok: true })
      : NextResponse.redirect(new URL('/?lead=success#lead', request.url), 303)
  }

  const missingFields = REQUIRED_FIELDS.filter((field) => readField(formData, field, FIELD_MAX[field]).length === 0)
  const privacyAccepted = formData.get('privacy') === 'on'

  if (missingFields.length > 0 || !privacyAccepted) {
    const error = '병원명, 진료과/지역, 연락처, 환자 질문, 개인정보 동의는 필수입니다.'
    if (wantsJson) {
      return NextResponse.json({ ok: false, error, missingFields }, { status: 400 })
    }
    return NextResponse.redirect(new URL('/?lead=invalid#lead', request.url), 303)
  }

  const consentVersion = readField(formData, 'consent_version', FIELD_MAX.consent_version) || 'v1.2026-05'
  const sourcePath = (() => {
    const value = formData.get('source_path')
    return typeof value === 'string' && value.trim().startsWith('/') ? value.trim().slice(0, 500) : '/'
  })()

  const payload = {
    clinic_name: readField(formData, 'clinicName', FIELD_MAX.clinicName),
    clinic_type: readField(formData, 'clinicType', FIELD_MAX.clinicType),
    contact: readField(formData, 'contact', FIELD_MAX.contact),
    question: readField(formData, 'question', FIELD_MAX.question),
    privacy: privacyAccepted,
    consent_version: consentVersion,
    source_path: sourcePath,
  }

  const apiBase = getApiBase(true)

  // 실제 방문자 IP를 백엔드로 전달한다. GCP LB 뒤에서는 XFF second-from-right가 실제
  // 방문자다 (lib/client-ip.ts — admin/lib/security.ts와 동일 정책).
  // CDX-M1: SITE_BFF_SECRET이 설정되면 X-BFF-Auth + X-Visitor-IP로 인증 전달 — 백엔드
  // get_request_ip가 XFF 체인보다 우선 채택한다(rate-limit key·consent_ip가 실제 방문자 기준).
  // secret 미설정 시 기존 XFF 전달로 동작하며, 백엔드는 BFF egress IP를 client로 본다
  // (스푸핑은 불가하나 per-visitor 정밀도는 떨어짐 — 백로그 CDX-M1 참조).
  const outboundHeaders: Record<string, string> = {
    'Content-Type': 'application/json',
  }
  const clientIp = clientIpFromForwardedHeaders(request.headers) ?? ''
  if (clientIp) {
    outboundHeaders['X-Forwarded-For'] = clientIp
    outboundHeaders['X-Real-IP'] = clientIp
    const bffSecret = (process.env.SITE_BFF_SECRET || '').trim()
    if (bffSecret) {
      outboundHeaders['X-BFF-Auth'] = bffSecret
      outboundHeaders['X-Visitor-IP'] = clientIp
    }
  }

  let response: Response
  try {
    response = await fetch(`${apiBase}/leads`, {
      method: 'POST',
      headers: outboundHeaders,
      body: JSON.stringify(payload),
      cache: 'no-store',
    })
  } catch {
    if (wantsJson) {
      return NextResponse.json({ ok: false, error: 'Upstream unreachable' }, { status: 502 })
    }
    return NextResponse.redirect(new URL('/?lead=error#lead', request.url), 303)
  }

  if (!response.ok) {
    // 업스트림 429(공개 리드 rate limit)는 입력 문제가 아니다 — 재시도 안내로 구분한다.
    // 입력 오류로 안내하면 사용자가 같은 내용을 계속 다시 제출하게 된다.
    if (response.status === 429) {
      const error = '요청이 많아 잠시 접수가 어렵습니다. 잠시 후 다시 시도해 주세요.'
      if (wantsJson) {
        return NextResponse.json({ ok: false, error, upstreamStatus: 429 }, { status: 429 })
      }
      return NextResponse.redirect(new URL('/?lead=busy#lead', request.url), 303)
    }

    // 업스트림 4xx(422 검증 실패 등)는 서버 장애가 아니라 입력 문제 — 입력 오류로 안내한다.
    const isValidationError = response.status >= 400 && response.status < 500
    if (isValidationError) {
      let detail: string | null = null
      try {
        const data = (await response.json()) as { detail?: unknown }
        // FastAPI는 detail이 문자열(HTTPException) 또는 배열(422 ValidationError)일 수 있다.
        if (typeof data?.detail === 'string') {
          detail = data.detail
        } else if (Array.isArray(data?.detail)) {
          detail = data.detail
            .map((item) => (typeof (item as { msg?: unknown })?.msg === 'string' ? (item as { msg: string }).msg : null))
            .filter((msg): msg is string => Boolean(msg))
            .join(' / ') || null
        }
      } catch {
        // 본문 파싱 실패 시 일반 입력 오류 안내로 충분.
      }
      if (wantsJson) {
        return NextResponse.json(
          {
            ok: false,
            error: detail || '입력하신 내용을 다시 확인해 주세요.',
            upstreamStatus: response.status,
          },
          { status: 400 },
        )
      }
      return NextResponse.redirect(new URL('/?lead=invalid#lead', request.url), 303)
    }

    const error = '무료 진단 요청을 접수하지 못했습니다. 잠시 후 다시 시도해 주세요.'
    if (wantsJson) {
      return NextResponse.json({ ok: false, error, upstreamStatus: response.status }, { status: 502 })
    }
    return NextResponse.redirect(new URL('/?lead=error#lead', request.url), 303)
  }

  if (wantsJson) {
    return NextResponse.json(await response.json())
  }
  return NextResponse.redirect(new URL('/?lead=success#lead', request.url), 303)
}
