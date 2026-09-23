import { keywordContainsHospitalName, parseKeywords } from './diagnosis-form.ts'

export type InquiryLeadFields = {
  clinicName: string
  clinicAddress: string
  directorName: string
  directorPhone: string
  homepage: string
  /** 진료과 — 초도 노출 진단의 슬롯 1 앵커. */
  specialty: string
  /** 지역 키워드 (예: 수서역). */
  regionKeyword: string
  /** 쉼표로 나눈 핵심 키워드 원문. 서버가 1~4개로 정리한다. */
  coreKeywords: string
}

export type InquiryLeadPayload = {
  clinic_name: string
  clinic_type: '도입문의'
  contact: string
  question: string
  contact_name: string
  specialty: string
  region_keyword: string
  core_keywords: string[]
}

export function isReasonableKoreanMobilePhone(value: string): boolean {
  return /^01[016789][ -]?\d{3,4}[ -]?\d{4}$/.test(value.trim())
}

export function isValidHttpUrl(value: string): boolean {
  try {
    const url = new URL(value.trim())
    return (url.protocol === 'http:' || url.protocol === 'https:') && Boolean(url.hostname)
  } catch {
    return false
  }
}

export type DiagnosisInputError = 'empty' | 'contains-clinic-name' | null

/**
 * 진료과·지역·키워드가 초도 진단 질의를 만들 수 있는 상태인지. 병원명이 질의에 섞이면
 * 언급이 보장돼 측정이 무의미해지므로 백엔드와 같은 규칙으로 앞단에서 끊는다.
 */
export function diagnosisInputError(fields: Pick<InquiryLeadFields, 'clinicName' | 'specialty' | 'regionKeyword' | 'coreKeywords'>): DiagnosisInputError {
  const keywords = parseKeywords(fields.coreKeywords)
  if (keywords.length === 0) return 'empty'
  if (keywordContainsHospitalName(fields.clinicName, [fields.specialty, fields.regionKeyword, ...keywords])) {
    return 'contains-clinic-name'
  }
  return null
}

/**
 * Label where the inquiry form was submitted from. Home page mounts ContactForm
 * at `#contact`; the dedicated `/contact` route must not be mis-labeled as `/#contact`.
 */
export function inquirySourcePath(pathname: string): '/contact' | '/#contact' {
  return pathname.startsWith('/contact') ? '/contact' : '/#contact'
}

/**
 * The upstream lead API keeps `clinic_type` as the 도입문의 marker, so the specialty
 * travels in its own field. The primary phone stays in `contact` and the other
 * free-text details are preserved as labelled lines in `question`, so none of them
 * are lost or mistaken for a free-form message.
 */
export function buildInquiryLeadPayload(fields: InquiryLeadFields): InquiryLeadPayload {
  return {
    clinic_name: fields.clinicName.trim(),
    clinic_type: '도입문의',
    contact: fields.directorPhone.trim(),
    contact_name: fields.directorName.trim(),
    specialty: fields.specialty.trim(),
    region_keyword: fields.regionKeyword.trim(),
    core_keywords: parseKeywords(fields.coreKeywords),
    question: [
      `병원 주소: ${fields.clinicAddress.trim()}`,
      `원장님 성함: ${fields.directorName.trim()}`,
      `병원 홈페이지: ${fields.homepage.trim()}`,
    ].join('\n'),
  }
}
