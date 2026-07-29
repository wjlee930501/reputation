/**
 * 무료 AI 노출 진단 신청 폼의 순수 로직 (PRD F1).
 *
 * 백엔드가 최종 검증자다. 여기 검증은 **왕복을 아끼고 오타를 잡기 위한 것**이지
 * 보안 경계가 아니다 — 클라이언트 검증만 믿으면 접수 API를 직접 때리면 그만이다.
 *
 * 다만 이 화면에는 백엔드가 대신해 줄 수 없는 책임이 하나 있다:
 * **한 병원당 한 번뿐이라는 사실을 확실히 인지시키는 것**(F1-6·F1-8).
 * 전화번호와 이메일이 영구 잠금이라, 오타 하나가 그 병원의 유일한 기회를 태운다.
 */

export type DiagnosisFormValues = {
  clinicName: string
  clinicType: string
  regionKeyword: string
  clinicPhone: string
  coreKeywords: string
  contactName: string
  contact: string
  email: string
  privacy: boolean
}

export const EMPTY_FORM: DiagnosisFormValues = {
  clinicName: '',
  clinicType: '',
  regionKeyword: '',
  clinicPhone: '',
  coreKeywords: '',
  contactName: '',
  contact: '',
  email: '',
  privacy: false,
}

export const MAX_KEYWORDS = 4

/** 쉼표로 나눈 키워드. 빈 값·중복을 걷어내고 순서는 유지한다 — 첫 번째가 질의 슬롯 2다. */
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

/** 표기 차이를 지운 비교용 형태 — 띄어쓰기만 바꾼 우회를 잡기 위해 백엔드와 같은 규칙을 쓴다. */
function normalizedForContainment(value: string): string {
  return (value || '').replace(/[\s\-_·]+/g, '').toLowerCase()
}

/**
 * 키워드에 병원명이 들어갔는가 (PRD F1-4).
 *
 * 병원명이 질의에 들어가면 언급은 보장되고 측정은 무의미해진다. 백엔드도 막지만,
 * 제출 후 거절보다 입력 중에 알려주는 편이 낫다.
 */
export function keywordContainsHospitalName(clinicName: string, keywords: string[]): boolean {
  const needle = normalizedForContainment(clinicName)
  if (!needle) return false
  return keywords.some((keyword) => normalizedForContainment(keyword).includes(needle))
}

/** 국내 번호는 숫자 9~15자리. 백엔드 정규화와 같은 범위를 쓴다. */
export function isUsablePhone(raw: string): boolean {
  const digits = (raw || '').replace(/\D+/g, '')
  return digits.length >= 9 && digits.length <= 15
}

export function isUsableEmail(raw: string): boolean {
  const value = (raw || '').trim()
  if (value.split('@').length !== 2) return false
  const [local, domain] = value.split('@')
  return Boolean(local) && domain.includes('.') && !domain.startsWith('.') && !domain.endsWith('.')
}

/**
 * 흔한 이메일 도메인 오탈자 교정 제안.
 *
 * 이중 영구 잠금 아래에서는 **오타 하나가 그 병원의 유일한 기회를 태운다.**
 * 작은 보정이지만 여기서는 값을 한다.
 */
const DOMAIN_CORRECTIONS: Record<string, string> = {
  'gmail.co': 'gmail.com',
  'gmail.con': 'gmail.com',
  'gmial.com': 'gmail.com',
  'gmai.com': 'gmail.com',
  'navr.com': 'naver.com',
  'naver.co': 'naver.com',
  'nate.co': 'nate.com',
  'daum.ne': 'daum.net',
  'hanmail.ne': 'hanmail.net',
  'kakao.co': 'kakao.com',
  'outlook.co': 'outlook.com',
  'hotmail.co': 'hotmail.com',
}

export function suggestEmailCorrection(raw: string): string | null {
  const value = (raw || '').trim().toLowerCase()
  const at = value.lastIndexOf('@')
  if (at < 1) return null
  const domain = value.slice(at + 1)
  const corrected = DOMAIN_CORRECTIONS[domain]
  if (!corrected || corrected === domain) return null
  return `${value.slice(0, at)}@${corrected}`
}

export type FieldErrors = Partial<Record<keyof DiagnosisFormValues, string>>

export function validateDiagnosisForm(values: DiagnosisFormValues): FieldErrors {
  const errors: FieldErrors = {}
  const keywords = parseKeywords(values.coreKeywords)

  if (values.clinicName.trim().length < 2) {
    errors.clinicName = '정식 병원명을 입력해 주세요. (예: 장편한외과의원)'
  }
  if (!values.clinicType.trim()) errors.clinicType = '진료과를 입력해 주세요.'
  if (!values.regionKeyword.trim()) {
    errors.regionKeyword = '지역 키워드를 입력해 주세요. (예: 수서역)'
  }
  if (!isUsablePhone(values.clinicPhone)) {
    errors.clinicPhone = '병원 대표번호를 정확히 입력해 주세요.'
  }
  if (keywords.length === 0) {
    errors.coreKeywords = '핵심 키워드를 1개 이상 입력해 주세요.'
  } else if (keywordContainsHospitalName(values.clinicName, keywords)) {
    errors.coreKeywords =
      '키워드에는 병원명을 넣을 수 없습니다. 진료·증상 키워드를 입력해 주세요.'
  }
  if (!values.contactName.trim()) errors.contactName = '담당자 이름을 입력해 주세요.'
  if (!values.contact.trim()) errors.contact = '담당자 연락처를 입력해 주세요.'
  if (!isUsableEmail(values.email)) errors.email = '리포트를 받으실 이메일을 정확히 입력해 주세요.'
  if (!values.privacy) errors.privacy = '개인정보 수집·이용에 동의해 주세요.'

  return errors
}

/** 확인 모달에 그대로 뿌리는 (라벨, 값) 목록 — 사용자가 입력한 것을 입력한 그대로 보여준다. */
export function confirmationRows(values: DiagnosisFormValues): { label: string; value: string }[] {
  return [
    { label: '병원명', value: values.clinicName.trim() },
    { label: '지역', value: values.regionKeyword.trim() },
    { label: '대표번호', value: values.clinicPhone.trim() },
    { label: '진료과', value: values.clinicType.trim() },
    { label: '키워드', value: parseKeywords(values.coreKeywords).join(', ') },
    { label: '담당자', value: `${values.contactName.trim()} · ${values.contact.trim()}` },
    { label: '이메일', value: values.email.trim() },
  ]
}

export function toRequestPayload(values: DiagnosisFormValues, sourcePath: string) {
  return {
    clinic_name: values.clinicName.trim(),
    clinic_type: values.clinicType.trim(),
    region_keyword: values.regionKeyword.trim(),
    clinic_phone: values.clinicPhone.trim(),
    core_keywords: parseKeywords(values.coreKeywords),
    contact_name: values.contactName.trim(),
    contact: values.contact.trim(),
    email: values.email.trim(),
    privacy: values.privacy,
    source_path: sourcePath,
  }
}
