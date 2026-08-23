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

/** 화면과 FAQPage 구조화 데이터가 함께 쓰는 질문 한 건. */
export interface FaqEntry {
  id: string
  question: string
  answer: string
  url: string
}

/**
 * FAQPage로 내보낼 질문 목록 (P-A-5).
 *
 * FAQ 유형으로 승인·발행된 콘텐츠의 질문과 답변 요약만 쓴다. 자유 입력 본문이나
 * 다른 유형의 제목을 질문처럼 끌어오지 않는다 — 의료광고 검수를 통과한 문장만
 * 병원 이름 옆에 붙어야 한다.
 *
 * 화면 FAQ 섹션과 JSON-LD가 이 함수를 함께 호출한다. 구조화 데이터에만 있고
 * 페이지에는 없는 Q&A는 검색·답변 엔진이 신뢰하지 않는 형태이므로, 두 출력이
 * 갈라질 수 없게 선택을 한곳에 둔다.
 */
export function selectFaqEntries(
  contents: ContentSummary[],
  hospitalRootUrl: string,
  limit: number = FAQ_MAX_ITEMS,
): FaqEntry[] {
  return contents
    .filter((c) => c.content_type === 'FAQ')
    .map((c) => ({
      id: c.id,
      question: (c.faq_question || c.title || '').trim(),
      answer: (c.faq_answer_summary || c.meta_description || '').trim(),
      url: `${hospitalRootUrl}/contents/${c.id}`,
    }))
    .filter((entry) => Boolean(entry.question && entry.answer))
    .slice(0, Math.max(0, limit))
}

// 발행된 FAQ들을 한 페이지의 FAQPage로 집계한다. 개별 FAQ 상세 페이지는 각자
// FAQPage를 갖지만, 랜딩(priority 0.8)·목록 페이지에는 집계 노드가 없어 답변엔진이
// 병원 단위 Q&A 세트를 한 번에 인지하지 못한다 — 이를 메운다.
export function buildFaqPageJsonLd(
  contents: ContentSummary[],
  hospitalRootUrl: string,
): Record<string, unknown> | null {
  const entries = selectFaqEntries(contents, hospitalRootUrl)
  if (entries.length === 0) return null

  return {
    '@context': 'https://schema.org',
    '@type': 'FAQPage',
    '@id': `${hospitalRootUrl}#faq`,
    mainEntity: entries.map((entry) => ({
      '@type': 'Question',
      name: entry.question,
      url: entry.url,
      acceptedAnswer: {
        '@type': 'Answer',
        text: entry.answer,
      },
    })),
  }
}
