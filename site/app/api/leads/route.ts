import { NextResponse } from 'next/server'
import { getApiBase } from '@/lib/config'
import { containsPatientSensitiveLeadText, leadSafetyError } from '@/lib/lead-safety'
import { buildLeadOutboundHeaders, isLeadValidationUpstreamStatus } from '@/lib/leads-proxy'
// 키워드 정리 규칙은 무료 진단 폼과 같은 것을 쓴다 — 두 벌로 나뉘면 한쪽만 백엔드
// `clean_keywords`와 어긋난 채로 남는다.
import { parseKeywords } from '@/lib/diagnosis-form'
import { BodyTooLargeError, readFormDataBodyWithLimit } from '@/lib/request-body'

export const runtime = 'nodejs'

/**
 * 진료과·지역·핵심 키워드는 선택이 아니다.
 *
 * 이 셋이 모두 와야 백엔드가 접수 즉시 초도 노출 진단을 만든다
 * (`LeadCreate.diagnosis_input` — 하나라도 비면 Admin 수동 생성으로 남는다).
 * 도입문의의 목적 자체가 "문의가 들어오면 보고서가 준비되어 있는 것"이므로,
 * 여기서 빼먹으면 폼은 접수되는데 정작 연락할 근거가 만들어지지 않는다.
 */
const REQUIRED_FIELDS = [
  'clinicName',
  'clinicType',
  'contact',
  'question',
  'specialty',
  'regionKeyword',
  'coreKeywords',
] as const
const FIELD_MAX = {
  clinicName: 200,
  clinicType: 200,
  contact: 200,
  question: 1000,
  specialty: 100,
  regionKeyword: 100,
  // 원시 입력은 넉넉히 받고 정리 뒤 4개로 자른다 — 키워드가 많다고 문의를 거절하지 않는다.
  coreKeywords: 400,
  contactName: 100,
  consent_version: 40,
} as const


const MAX_BODY_BYTES = 64 * 1024 // 64KB — 정상 폼은 ~1KB. 그 이상이면 abuse.

function readField(formData: FormData, field: string, max: number) {
  const value = formData.get(field)
  if (typeof value !== 'string') return ''
  return value.trim().slice(0, max)
}

export async function POST(request: Request) {
  // JSON을 요청한 쪽(랜딩 도입문의 폼)은 이 `error` 문자열을 그대로 화면에 띄운다.
  // 개발용 영문 메시지를 남겨 두면 원장이 그것을 읽게 된다.
  const wantsJson = request.headers.get('accept')?.includes('application/json') ?? false

  let formData: FormData
  try {
    formData = await readFormDataBodyWithLimit(request, MAX_BODY_BYTES)
  } catch (error) {
    if (error instanceof BodyTooLargeError) {
      return wantsJson
        ? NextResponse.json({ ok: false, error: '입력 내용이 너무 깁니다. 문의 내용을 줄여 다시 시도해 주세요.' }, { status: 413 })
        : NextResponse.redirect(new URL('/?lead=invalid#lead', request.url), 303)
    }
    return wantsJson
      ? NextResponse.json({ ok: false, error: '입력 형식을 확인할 수 없습니다. 화면을 새로고침한 뒤 다시 시도해 주세요.' }, { status: 400 })
      : NextResponse.redirect(new URL('/?lead=invalid#lead', request.url), 303)
  }

  // Honeypot — 봇이 자동으로 채우는 필드. 채워져 있으면 silently 200.
  const honey = readField(formData, 'website', 500)
  if (honey) {
    return wantsJson
      ? NextResponse.json({ ok: true })
      : NextResponse.redirect(new URL('/?lead=success#lead', request.url), 303)
  }

  const coreKeywords = parseKeywords(readField(formData, 'coreKeywords', FIELD_MAX.coreKeywords))
  const missingFields = REQUIRED_FIELDS.filter((field) => (
    field === 'coreKeywords'
      ? coreKeywords.length === 0
      : readField(formData, field, FIELD_MAX[field]).length === 0
  ))
  const privacyAccepted = formData.get('privacy') === 'on'

  if (missingFields.length > 0 || !privacyAccepted) {
    const error = '병원명, 진료과, 지역, 핵심 키워드, 연락처, 문의 내용, 개인정보 동의는 필수입니다.'
    if (wantsJson) {
      return NextResponse.json({ ok: false, error, missingFields }, { status: 400 })
    }
    return NextResponse.redirect(new URL('/?lead=invalid#lead', request.url), 303)
  }

  const question = readField(formData, 'question', FIELD_MAX.question)
  const specialty = readField(formData, 'specialty', FIELD_MAX.specialty)
  const regionKeyword = readField(formData, 'regionKeyword', FIELD_MAX.regionKeyword)
  const contactName = readField(formData, 'contactName', FIELD_MAX.contactName)
  // 자유 텍스트는 question만이 아니다 — 진단 입력도 Slack과 Admin에 그대로 나간다.
  // 백엔드가 최종 검증자지만, 왕복 전에 같은 규칙으로 걸러 이유를 바로 보여준다.
  const freeText = [question, specialty, regionKeyword, contactName, ...coreKeywords]
  if (freeText.some((value) => value.length > 0 && containsPatientSensitiveLeadText(value))) {
    const error = leadSafetyError()
    if (wantsJson) {
      return NextResponse.json({ ok: false, error }, { status: 400 })
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
    question,
    privacy: privacyAccepted,
    consent_version: consentVersion,
    source_path: sourcePath,
    specialty,
    region_keyword: regionKeyword,
    core_keywords: coreKeywords,
    ...(contactName ? { contact_name: contactName } : {}),
  }

  const apiBase = getApiBase(true)

  // 실제 방문자 IP를 백엔드로 전달한다. GCP LB 뒤에서는 XFF second-from-right가 실제
  // 방문자다 (lib/client-ip.ts — admin/lib/security.ts와 동일 정책).
  // CDX-M1: SITE_BFF_SECRET이 설정되면 X-BFF-Auth + X-Visitor-IP로 인증 전달 — 백엔드
  // get_request_ip가 XFF 체인보다 우선 채택한다(rate-limit key·consent_ip가 실제 방문자 기준).
  // secret 미설정 시 기존 XFF 전달로 동작하며, 백엔드는 BFF egress IP를 client로 본다
  // (스푸핑은 불가하나 per-visitor 정밀도는 떨어짐 — 백로그 CDX-M1 참조).
  const outboundHeaders = buildLeadOutboundHeaders(request.headers)

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
      return NextResponse.json({ ok: false, error: '접수 서버에 연결하지 못했습니다. 잠시 후 다시 시도해 주세요.' }, { status: 502 })
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
    const isValidationError = isLeadValidationUpstreamStatus(response.status)
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

    const error = '도입 문의를 접수하지 못했습니다. 잠시 후 다시 시도해 주세요.'
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
