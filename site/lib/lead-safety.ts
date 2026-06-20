const RESIDENT_REGISTRATION_NUMBER = /\b\d{6}[-\s]?[1-4]\d{6}\b/
const PATIENT_RECORD_CONTEXT = /(수술\s*기록|진료\s*기록|진료\s*내역|의무\s*기록|검사\s*결과|처방\s*내역|처방전|차트)/
const PATIENT_RECORD_WITH_PERSON_CONTEXT =
  /(환자|보호자).{0,30}(주민등록|진료\s*기록|진료\s*내역|수술\s*기록|의무\s*기록|검사\s*결과|처방\s*내역|처방전|차트)/
const NATIONAL_ID_CONTEXT = /주민등록(?:번호)?/
const PERSONAL_IDENTIFIER_CONTEXT = /(연락처|전화번호|휴대폰|생년월일|환자\s*번호)/

export function containsPatientSensitiveLeadText(value: string): boolean {
  const normalized = value.trim().replace(/\s+/g, ' ')
  if (!normalized) return false
  if (RESIDENT_REGISTRATION_NUMBER.test(normalized)) return true
  if (PATIENT_RECORD_CONTEXT.test(normalized)) return true
  if (PATIENT_RECORD_WITH_PERSON_CONTEXT.test(normalized)) return true
  if (NATIONAL_ID_CONTEXT.test(normalized)) return true
  return PERSONAL_IDENTIFIER_CONTEXT.test(normalized) && /\d{4,}/.test(normalized)
}

export function leadSafetyError(): string {
  return '환자 개인정보는 무료 진단 요청에 포함할 수 없습니다.'
}
