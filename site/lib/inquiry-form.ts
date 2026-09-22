/**
 * 도입 문의 폼의 순수 로직.
 *
 * 무료 진단 폼(`diagnosis-form.ts`)과 목적이 다르다. 그쪽은 신청자가 스스로 리포트를
 * 받아가는 셀프서브라 이메일·1회 영구 잠금이 핵심이고, 이쪽은 **문의가 들어오면 시스템이
 * 초도 노출 진단을 만들어 두고 우리가 그 결과로 연락하는** 경로다. 그래서 이메일도,
 * 선착순 자리도 받지 않는 대신 진료과·지역·핵심 키워드를 반드시 받는다 — 이 셋이 있어야
 * 백엔드가 접수 즉시 진단을 만든다(`LeadCreate.diagnosis_input`).
 *
 * 백엔드가 최종 검증자다. 여기 검증은 왕복을 아끼고 오타를 잡기 위한 것이지 보안 경계가
 * 아니다.
 */
import { MAX_KEYWORDS, keywordContainsHospitalName, isUsablePhone, parseKeywords } from './diagnosis-form.ts'
import { containsPatientSensitiveLeadText } from './lead-safety.ts'

export type InquiryFormValues = {
  clinicName: string
  specialty: string
  regionKeyword: string
  coreKeywords: string
  contactName: string
  contact: string
  question: string
  privacy: boolean
}

export const EMPTY_INQUIRY: InquiryFormValues = {
  clinicName: '',
  specialty: '',
  regionKeyword: '',
  coreKeywords: '',
  contactName: '',
  contact: '',
  question: '',
  privacy: false,
}

/**
 * `clinic_type`에 들어가는 고정 표식.
 *
 * 진료과는 `specialty`로 따로 보낸다 — 이 칸을 진료과가 덮으면 Admin 리드 목록에서
 * 도입문의와 무료진단 신청을 구분할 수 없다(docs/ops/inquiry-intake-automation.md).
 */
export const INQUIRY_CLINIC_TYPE = '도입문의'

export type InquiryFieldErrors = Partial<Record<keyof InquiryFormValues, string>>

export function validateInquiryForm(values: InquiryFormValues): InquiryFieldErrors {
  const errors: InquiryFieldErrors = {}
  const keywords = parseKeywords(values.coreKeywords)

  if (values.clinicName.trim().length < 2) {
    errors.clinicName = '정식 병원명을 입력해 주세요. (예: 장편한외과의원)'
  }
  if (!values.specialty.trim()) errors.specialty = '진료과를 입력해 주세요. (예: 외과)'
  if (!values.regionKeyword.trim()) {
    errors.regionKeyword = '지역을 입력해 주세요. (예: 수서역)'
  }
  if (keywords.length === 0) {
    errors.coreKeywords = `핵심 키워드를 1개 이상 입력해 주세요. (최대 ${MAX_KEYWORDS}개)`
  } else if (keywordContainsHospitalName(values.clinicName, keywords)) {
    // 병원명이 질의에 들어가면 언급은 보장되고 측정은 무의미해진다.
    errors.coreKeywords =
      '키워드에는 병원명을 넣을 수 없습니다. 진료·증상 키워드를 입력해 주세요.'
  }
  if (!values.contactName.trim()) errors.contactName = '원장님 성함을 입력해 주세요.'
  if (!isUsablePhone(values.contact)) {
    // 안내 문자가 나가는 번호다 — 형식이 어긋나면 접수는 되고 연락은 안 된다.
    errors.contact = '연락 가능한 휴대전화 번호를 정확히 입력해 주세요.'
  }
  if (!values.question.trim()) errors.question = '문의 내용을 한 줄이라도 남겨 주세요.'

  // 진료과·지역·키워드·문의 내용은 모두 Slack과 Admin에 그대로 나간다.
  const sensitive: [keyof InquiryFormValues, string][] = [
    ['clinicName', values.clinicName],
    ['specialty', values.specialty],
    ['regionKeyword', values.regionKeyword],
    ['coreKeywords', values.coreKeywords],
    ['contactName', values.contactName],
    ['question', values.question],
  ]
  for (const [field, value] of sensitive) {
    if (value.trim() && containsPatientSensitiveLeadText(value)) {
      errors[field] = '환자 개인정보나 진료기록은 이 문의 양식에 입력하지 마세요.'
    }
  }

  if (!values.privacy) errors.privacy = '개인정보 수집·이용에 동의해 주세요.'
  return errors
}

/** 프록시(`/api/leads`)가 읽는 FormData. 필드 이름은 라우트의 REQUIRED_FIELDS와 같아야 한다. */
export function toInquiryFormData(values: InquiryFormValues, sourcePath: string): FormData {
  const form = new FormData()
  form.set('clinicName', values.clinicName.trim())
  form.set('clinicType', INQUIRY_CLINIC_TYPE)
  form.set('specialty', values.specialty.trim())
  form.set('regionKeyword', values.regionKeyword.trim())
  form.set('coreKeywords', parseKeywords(values.coreKeywords).join(', '))
  form.set('contactName', values.contactName.trim())
  form.set('contact', values.contact.trim())
  form.set('question', values.question.trim())
  form.set('privacy', values.privacy ? 'on' : '')
  form.set('source_path', sourcePath)
  return form
}
