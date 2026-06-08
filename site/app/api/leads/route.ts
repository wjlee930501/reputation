import { NextResponse } from 'next/server'
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

  // 실제 방문자 IP를 백엔드로 전달한다. 누락 시 rate-limit이 단일 egress IP 버킷으로
  // 무너지고 PIPA 동의 IP(consent_ip)가 데이터센터 IP로 잘못 저장된다.
  const outboundHeaders: Record<string, string> = {
    'Content-Type': 'application/json',
  }
  const forwardedFor = request.headers.get('x-forwarded-for')
  const realIp = request.headers.get('x-real-ip')
  const clientIp = (forwardedFor?.split(',')[0] || realIp || '').trim()
  if (clientIp) {
    outboundHeaders['X-Forwarded-For'] = clientIp
    outboundHeaders['X-Real-IP'] = clientIp
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
