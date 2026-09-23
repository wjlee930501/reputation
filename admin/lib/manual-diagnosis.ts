/**
 * 콜용 노출 진단을 사람이 직접 만드는 폼의 순수 로직.
 *
 * 두 곳이 같은 계약을 쓴다 — 새 탭(`/diagnoses`)의 신규 생성과, 상담 요청 화면에서
 * 틀린 값을 고쳐 다시 만드는 경로다. 뒤쪽은 `leadId`를 함께 보내며 서버가 기존 활성
 * 진단을 갈음한다.
 *
 * 백엔드가 최종 검증자다(`ManualDiagnosisRequest`). 여기 검증은 왕복을 아끼고
 * 무엇이 왜 필요한지 화면에서 말해 주기 위한 것이다.
 */

export type ManualDiagnosisValues = {
  clinicName: string
  specialty: string
  regionKeyword: string
  coreKeywords: string
  contact: string
  contactName: string
  reason: string
}

export const EMPTY_MANUAL_DIAGNOSIS: ManualDiagnosisValues = {
  clinicName: '',
  specialty: '',
  regionKeyword: '',
  coreKeywords: '',
  contact: '',
  contactName: '',
  reason: '',
}

export const MAX_KEYWORDS = 4

/** 쉼표로 나눈 키워드. 빈 값·중복을 걷고 순서를 유지한다(백엔드 clean_keywords와 같은 규칙). */
export function parseKeywords(raw: string): string[] {
  const seen = new Set<string>()
  const out: string[] = []
  for (const piece of (raw || '').split(',')) {
    const value = piece.trim()
    if (!value || seen.has(value)) continue
    seen.add(value)
    out.push(value)
    if (out.length === MAX_KEYWORDS) break
  }
  return out
}

/** 표기 차이를 지운 비교용 형태 — 백엔드 판정과 같은 규칙을 쓴다. */
function normalized(value: string): string {
  return (value || '').replace(/[\s\-_·]+/g, '').toLowerCase()
}

/**
 * 입력값에 병원명이 섞였는가.
 *
 * 질의에 병원명이 들어가면 AI는 당연히 그 병원을 말한다 — 언급은 보장되고 측정은
 * 무의미해진다. 서버도 막지만 제출 후 거절보다 입력 중에 알려주는 편이 낫다.
 */
export function inputContainsClinicName(clinicName: string, values: string[]): boolean {
  const needle = normalized(clinicName)
  if (!needle) return false
  return values.some((value) => normalized(value).includes(needle))
}

export type ManualDiagnosisErrors = Partial<Record<keyof ManualDiagnosisValues, string>>

export function validateManualDiagnosis(
  values: ManualDiagnosisValues,
  { requireContact = true }: { requireContact?: boolean } = {},
): ManualDiagnosisErrors {
  const errors: ManualDiagnosisErrors = {}
  const keywords = parseKeywords(values.coreKeywords)

  if (values.clinicName.trim().length < 2) {
    errors.clinicName = '간판에 적힌 정식 병원명을 입력해 주세요. (예: 장편한외과의원)'
  }
  if (!values.specialty.trim()) errors.specialty = '진료과를 입력해 주세요. (예: 정형외과)'
  if (!values.regionKeyword.trim()) {
    errors.regionKeyword = '지역을 입력해 주세요. 지하철역 또는 동 이름이 좋습니다.'
  }
  if (keywords.length === 0) {
    errors.coreKeywords = `핵심 키워드를 1개 이상 입력해 주세요. (최대 ${MAX_KEYWORDS}개)`
  } else if (
    inputContainsClinicName(values.clinicName, [
      values.specialty,
      values.regionKeyword,
      ...keywords,
    ])
  ) {
    errors.coreKeywords =
      '진료과·지역·키워드에 병원명을 넣을 수 없습니다. 병원명이 들어가면 측정이 무의미해집니다.'
  }
  // 고쳐 만드는 경로는 리드에 이미 연락처가 있다.
  if (requireContact && !values.contact.trim()) {
    errors.contact = '연락할 수단을 남겨 주세요. 병원 대표번호도 괜찮습니다.'
  }
  if (values.reason.trim().length < 3) {
    errors.reason = '왜 직접 만드는지 3자 이상 적어 주세요. 기록에 그대로 남습니다.'
  }
  return errors
}

export type ManualDiagnosisPayload = {
  clinic_name: string
  specialty: string
  region_keyword: string
  core_keywords: string[]
  /** 고쳐 만드는 경로에서는 보내지 않는다 — 원장이 남긴 연락처를 덮지 않는다. */
  contact?: string
  contact_name?: string
  lead_id?: string
  reason: string
}

export function toManualDiagnosisPayload(
  values: ManualDiagnosisValues,
  leadId?: string,
): ManualDiagnosisPayload {
  const contactName = values.contactName.trim()
  return {
    clinic_name: values.clinicName.trim(),
    specialty: values.specialty.trim(),
    region_keyword: values.regionKeyword.trim(),
    core_keywords: parseKeywords(values.coreKeywords),
    ...(leadId ? {} : { contact: values.contact.trim() }),
    ...(contactName ? { contact_name: contactName } : {}),
    ...(leadId ? { lead_id: leadId } : {}),
    reason: values.reason.trim(),
  }
}

/**
 * 서버가 알려준 거절 사유 중 운영자가 고칠 수 있는 것만 꺼낸다.
 *
 * 갈음 거절(409)은 `{message, diagnosis_id}` 객체로, 입력 검증(422)은 배열로 온다.
 * 문자열만 보면 둘 다 일반 안내로 떨어져 AE가 무엇을 고쳐야 하는지 모른다.
 */
export function manualDiagnosisRefusal(
  detail: unknown,
  status: number,
  message: string,
): string | null {
  if (typeof detail === 'string' && detail.trim()) return detail
  if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
    const text = (detail as { message?: unknown }).message
    if (typeof text === 'string' && text.trim()) return text
  }
  if (status === 422 && message.trim()) return message
  return null
}
