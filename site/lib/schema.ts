import type { ContentSummary, Hospital } from './api.ts'

const FAQ_MAX_ITEMS = 10

// 원장 Physician 노드의 신뢰축(자격·학회·전문영역) 서브필드.
// 랜딩(중첩 Physician)과 /doctor(독립 Physician)가 동일 값을 내보내도록 공유한다 —
// 최우선순위 URL(랜딩 priority 0.8)에서도 약한 Physician이 되지 않게 한다.
export function buildPhysicianCredentials(hospital: Hospital): Record<string, unknown> {
  const credentials = hospital.director_credentials
  const boardCerts = credentials?.board_certifications ?? []
  const societies = credentials?.society_memberships ?? []
  const treatmentNames = (hospital.treatments || []).map((t) => t.name).filter(Boolean)
  const knowsAbout = Array.from(new Set([...(hospital.specialties || []), ...treatmentNames]))

  const hasCredential = boardCerts.map((name) => ({
    '@type': 'EducationalOccupationalCredential',
    credentialCategory: 'medical specialty board certification',
    name,
  }))
  const memberOf = societies.map((name) => ({ '@type': 'MedicalOrganization', name }))
  const alumniOf = credentials?.medical_school
    ? { '@type': 'EducationalOrganization', name: credentials.medical_school }
    : undefined

  // undefined 키는 JSON.stringify가 제거하므로 빈 배열만 걸러 키를 생략한다.
  return {
    medicalSpecialty: hospital.specialties?.length ? hospital.specialties : undefined,
    knowsAbout: knowsAbout.length > 0 ? knowsAbout : undefined,
    hasCredential: hasCredential.length > 0 ? hasCredential : undefined,
    memberOf: memberOf.length > 0 ? memberOf : undefined,
    alumniOf,
  }
}

// 발행된 FAQ들을 한 페이지의 FAQPage로 집계한다. 개별 FAQ 상세 페이지는 각자
// FAQPage를 갖지만, 랜딩(priority 0.8)·목록 페이지에는 집계 노드가 없어 답변엔진이
// 병원 단위 Q&A 세트를 한 번에 인지하지 못한다 — 이를 메운다.
export function buildFaqPageJsonLd(
  contents: ContentSummary[],
  hospitalRootUrl: string,
): Record<string, unknown> | null {
  const faqs = contents.filter(
    (c) =>
      c.content_type === 'FAQ' &&
      (c.faq_question || c.title) &&
      (c.faq_answer_summary || c.meta_description),
  )
  if (faqs.length === 0) return null

  return {
    '@context': 'https://schema.org',
    '@type': 'FAQPage',
    '@id': `${hospitalRootUrl}#faq`,
    mainEntity: faqs.slice(0, FAQ_MAX_ITEMS).map((c) => ({
      '@type': 'Question',
      name: c.faq_question || c.title,
      url: `${hospitalRootUrl}/contents/${c.id}`,
      acceptedAnswer: {
        '@type': 'Answer',
        text: c.faq_answer_summary || c.meta_description || '',
      },
    })),
  }
}
