import { getApiBase } from './config.ts'
import {
  buildInquiryLeadPayload,
  diagnosisInputError,
  isReasonableKoreanMobilePhone,
  isValidHttpUrl,
  type InquiryLeadFields,
} from './inquiry-lead.ts'
import { containsPatientSensitiveLeadText, leadSafetyError } from './lead-safety.ts'
import { buildLeadOutboundHeaders, isLeadValidationUpstreamStatus } from './leads-proxy.ts'
import { BodyTooLargeError, readFormDataBodyWithLimit } from './request-body.ts'

const REQUIRED_FIELDS = [
  'clinicName',
  'clinicAddress',
  'directorName',
  'directorPhone',
  'homepage',
  'specialty',
  'regionKeyword',
  'coreKeywords',
] as const
const FIELD_MAX = {
  clinicName: 200,
  clinicAddress: 300,
  directorName: 100,
  directorPhone: 40,
  homepage: 500,
  specialty: 100,
  regionKeyword: 100,
  coreKeywords: 300,
  consent_version: 40,
} as const
const MAX_BODY_BYTES = 64 * 1024 // 64KB — 정상 폼은 ~1KB. 그 이상이면 abuse.

function readField(formData: FormData, field: string, max: number) {
  const value = formData.get(field)
  if (typeof value !== 'string') return ''
  return value.trim().slice(0, max)
}

export async function POST(request: Request) {
  // JSON을 요청한 쪽(도입문의 폼)은 이 `error` 문자열을 그대로 화면에 띄운다.
  // 개발용 영문 메시지를 남겨 두면 원장이 그것을 읽게 된다.
  const wantsJson = request.headers.get('accept')?.includes('application/json') ?? false

  let formData: FormData
  try {
    formData = await readFormDataBodyWithLimit(request, MAX_BODY_BYTES)
  } catch (error) {
    if (error instanceof BodyTooLargeError) {
      return wantsJson
        ? Response.json({ ok: false, error: '입력 내용이 너무 깁니다. 문의 내용을 줄여 다시 시도해 주세요.' }, { status: 413 })
        : Response.redirect(new URL('/?lead=invalid#contact', request.url), 303)
    }
    return wantsJson
      ? Response.json({ ok: false, error: '입력 형식을 확인할 수 없습니다. 화면을 새로고침한 뒤 다시 시도해 주세요.' }, { status: 400 })
      : Response.redirect(new URL('/?lead=invalid#contact', request.url), 303)
  }

  // Honeypot — 봇이 자동으로 채우는 필드. 채워져 있으면 silently 200.
  const honey = readField(formData, 'website', 500)
  if (honey) {
    return wantsJson
      ? Response.json({ ok: true })
      : Response.redirect(new URL('/?lead=success#contact', request.url), 303)
  }

  const missingFields = REQUIRED_FIELDS.filter((field) => readField(formData, field, FIELD_MAX[field]).length === 0)
  const privacyAccepted = formData.get('privacy') === 'on'

  if (missingFields.length > 0 || !privacyAccepted) {
    const error =
      '병원명, 병원 주소, 원장님 성함, 원장님 연락처, 병원 홈페이지, 진료과, 지역 키워드, 핵심 진료 항목, 개인정보 동의는 필수입니다.'
    if (wantsJson) {
      return Response.json({ ok: false, error, missingFields }, { status: 400 })
    }
    return Response.redirect(new URL('/?lead=invalid#contact', request.url), 303)
  }

  const fields = Object.fromEntries(
    REQUIRED_FIELDS.map((field) => [field, readField(formData, field, FIELD_MAX[field])]),
  ) as InquiryLeadFields

  if (!isReasonableKoreanMobilePhone(fields.directorPhone)) {
    const error = '원장님 연락처 형식을 확인해 주세요.'
    if (wantsJson) {
      return Response.json({ ok: false, error, invalidFields: ['directorPhone'] }, { status: 400 })
    }
    return Response.redirect(new URL('/?lead=invalid#contact', request.url), 303)
  }

  if (!isValidHttpUrl(fields.homepage)) {
    const error = '병원 홈페이지는 http:// 또는 https://로 시작하는 주소여야 합니다.'
    if (wantsJson) {
      return Response.json({ ok: false, error, invalidFields: ['homepage'] }, { status: 400 })
    }
    return Response.redirect(new URL('/?lead=invalid#contact', request.url), 303)
  }

  // 진료과·지역·키워드는 초도 노출 진단 질의가 된다. 병원명이 섞이면 백엔드가 거절하고
  // 진단이 자동 시작되지 않으므로, 제출 전에 여기서 알려준다.
  const diagnosisError = diagnosisInputError(fields)
  if (diagnosisError) {
    const error =
      diagnosisError === 'empty'
        ? '핵심 진료 항목을 1개 이상 입력해 주세요.'
        : '진료과·지역·진료 항목에는 병원명을 넣을 수 없습니다. 진료 항목이나 증상을 입력해 주세요.'
    if (wantsJson) {
      return Response.json({ ok: false, error, invalidFields: ['coreKeywords'] }, { status: 400 })
    }
    return Response.redirect(new URL('/?lead=invalid#contact', request.url), 303)
  }

  const lead = buildInquiryLeadPayload(fields)
  if (
    [lead.question, lead.specialty, lead.region_keyword, lead.contact_name, ...lead.core_keywords].some(containsPatientSensitiveLeadText)
  ) {
    const error = leadSafetyError()
    if (wantsJson) {
      return Response.json({ ok: false, error }, { status: 400 })
    }
    return Response.redirect(new URL('/?lead=invalid#contact', request.url), 303)
  }

  const consentVersion = readField(formData, 'consent_version', FIELD_MAX.consent_version) || 'v1.2026-08'
  const sourcePath = (() => {
    const value = formData.get('source_path')
    return typeof value === 'string' && value.trim().startsWith('/') ? value.trim().slice(0, 500) : '/'
  })()

  const payload = {
    ...lead,
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
      return Response.json({ ok: false, error: '접수 서버에 연결하지 못했습니다. 잠시 후 다시 시도해 주세요.' }, { status: 502 })
    }
    return Response.redirect(new URL('/?lead=error#contact', request.url), 303)
  }

  if (!response.ok) {
    // 업스트림 429(공개 리드 rate limit)는 입력 문제가 아니다 — 재시도 안내로 구분한다.
    // 입력 오류로 안내하면 사용자가 같은 내용을 계속 다시 제출하게 된다.
    if (response.status === 429) {
      const error = '요청이 많아 잠시 접수가 어렵습니다. 잠시 후 다시 시도해 주세요.'
      if (wantsJson) {
        return Response.json({ ok: false, error, upstreamStatus: 429 }, { status: 429 })
      }
      return Response.redirect(new URL('/?lead=busy#contact', request.url), 303)
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
        return Response.json(
          {
            ok: false,
            error: detail || '입력하신 내용을 다시 확인해 주세요.',
            upstreamStatus: response.status,
          },
          { status: 400 },
        )
      }
      return Response.redirect(new URL('/?lead=invalid#contact', request.url), 303)
    }

    const error = '도입 문의를 접수하지 못했습니다. 잠시 후 다시 시도해 주세요.'
    if (wantsJson) {
      return Response.json({ ok: false, error, upstreamStatus: response.status }, { status: 502 })
    }
    return Response.redirect(new URL('/?lead=error#contact', request.url), 303)
  }

  // 안내 문자와 초도 진단은 백엔드가 리드 저장 뒤에 처리하고 결과를 응답에 실어 준다
  // (`ack_sms`: sent/failed/skipped, `diagnosis_id`). 랜딩은 그 값을 그대로 넘긴다.
  if (wantsJson) {
    return Response.json(await response.json())
  }
  return Response.redirect(new URL('/?lead=success#contact', request.url), 303)
}
