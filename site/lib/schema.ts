import type { ContentSummary, Hospital } from './api.ts'
import type { ClinicPhysician } from './clinic-physicians.ts'

const FAQ_MAX_ITEMS = 10

// 의료진 Physician 노드의 신뢰축(자격·학회·전문영역) 서브필드.
// 랜딩(employee[])과 /doctor(독립 Physician)가 동일 값을 내보내도록 공유한다 —
// 최우선순위 URL(랜딩 priority 0.8)에서도 약한 Physician이 되지 않게 한다.
//
// `physician`을 주면 그 사람의 자격·전문영역을 쓰고, 비어 있는 축만 병원 프로파일에서
// 채운다. 주지 않으면 종전대로 병원 대표 원장 한 명으로 본다.
export function buildPhysicianCredentials(
  hospital: Hospital,
  physician?: ClinicPhysician,
): Record<string, unknown> {
  const credentials = physician ? physician.credentials : hospital.director_credentials
  const boardCerts = credentials?.board_certifications ?? []
  const societies = credentials?.society_memberships ?? []
  const treatmentNames = (hospital.treatments || []).map((t) => t.name).filter(Boolean)
  const specialties =
    physician && physician.specialties.length > 0 ? physician.specialties : hospital.specialties || []
  const knowsAbout = Array.from(new Set([...specialties, ...treatmentNames]))

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
    medicalSpecialty: specialties.length > 0 ? specialties : undefined,
    knowsAbout: knowsAbout.length > 0 ? knowsAbout : undefined,
    hasCredential: hasCredential.length > 0 ? hasCredential : undefined,
    memberOf: memberOf.length > 0 ? memberOf : undefined,
    alumniOf,
  }
}

/**
 * 의료진 한 명의 Physician 노드. 랜딩의 `employee[]`와 `/doctor`가 같은 `@id`로
 * 같은 사람을 주장하도록 한 함수에서 만든다.
 */
export function buildPhysicianNode(input: {
  hospital: Hospital
  physician: ClinicPhysician
  hospitalRootUrl: string
  nodeId: string
  /** 절대 URL로 정규화된 인물 사진. 검증된 자산이 없으면 null. */
  imageUrl: string | null
  /** 약력 외에 덧붙일 문장(승인된 공개 서사 등). */
  extraDescription?: string | null
  sameAs?: string[]
}): Record<string, unknown> {
  const { hospital, physician, hospitalRootUrl, nodeId, imageUrl } = input
  const description =
    [physician.career, input.extraDescription?.trim() || null].filter(Boolean).join(' ') || undefined

  return {
    '@type': 'Physician',
    '@id': nodeId,
    name: physician.name,
    jobTitle: physician.title || '원장',
    description,
    image: imageUrl || undefined,
    url: `${hospitalRootUrl}/doctor`,
    ...buildPhysicianCredentials(hospital, physician),
    sameAs: input.sameAs && input.sameAs.length > 0 ? input.sameAs : undefined,
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
 * **승인된 질문(`faq_question`)과 검수된 답변 요약(`faq_answer_summary`)이 둘 다
 * 있는 항목만 쓴다.** 제목이나 메타 설명으로 대체하지 않는다.
 *
 * 이전 구현은 `faq_question || title`, `faq_answer_summary || meta_description`으로
 * 물러섰다. 그러면 `Question`/`Answer`로 나가는 문장이 운영자가 질문·답변으로
 * 승인한 문장이 아니라 목록용 제목과 검색 결과용 설명이 된다. 답변 엔진은 그
 * 문장을 병원이 답한 내용으로 인용하는데, 제목은 질문 형태가 아니고 메타 설명은
 * 답변으로 검수된 적이 없다. 즉 없는 Q&A를 지어내는 것과 같다.
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
  // 같은 질문 문장의 FAQ 글이 여러 편 있으면(측정 질문 하나로 여러 달 글을 쓴 경우) 처음 것
  // 하나만 싣는다 — 행복드림 홈에 '경산 내과 병원 추천해 주시겠어요?'가 세 번 나왔다(2026-11-20).
  // 상한을 자르기 전에 걸러 빈자리를 다음 질문이 채운다. 비교는 띄어쓰기·문장부호만 접은
  // 같은 문장뿐이다 — 비슷한 질문을 묶는 것은 승인된 질문을 고르는 일이라 하지 않는다.
  const seen = new Set<string>()
  return contents
    .filter((c) => c.content_type === 'FAQ')
    .map((c) => ({
      id: c.id,
      question: (c.faq_question ?? '').trim(),
      answer: (c.faq_answer_summary ?? '').trim(),
      url: `${hospitalRootUrl}/contents/${c.id}`,
    }))
    .filter((entry) => Boolean(entry.question && entry.answer))
    .filter((entry) => {
      const key = faqQuestionKey(entry.question)
      // 문장부호·기호뿐이라 비교 키가 비면 중복으로 보지 않는다(서로 다른 질문을 하나로 묶지 않게).
      if (!key) return true
      if (seen.has(key)) return false
      seen.add(key)
      return true
    })
    .slice(0, Math.max(0, limit))
}

// 발행된 FAQ들을 한 페이지의 FAQPage로 집계한다. 개별 FAQ 상세 페이지는 각자
// FAQPage를 갖지만, 랜딩(priority 0.8)·목록 페이지에는 집계 노드가 없어 답변엔진이
// 병원 단위 Q&A 세트를 한 번에 인지하지 못한다 — 이를 메운다.
export function buildFaqPageJsonLd(
  contents: ContentSummary[],
  hospitalRootUrl: string,
  pageUrl: string = hospitalRootUrl,
): Record<string, unknown> | null {
  const entries = selectFaqEntries(contents, hospitalRootUrl)
  if (entries.length === 0) return null

  return {
    '@context': 'https://schema.org',
    '@type': 'FAQPage',
    '@id': `${pageUrl}#faq`,
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

/**
 * 질문 문장 비교 키 — 유니코드 정규화(NFKC), 소문자, 띄어쓰기·문장부호 제거.
 * '경산 내과 병원 추천해 주시겠어요?'와 '경산 내과 병원  추천해 주시겠어요 ?'는 같은 질문이다.
 */
export function faqQuestionKey(question: string): string {
  return question
    .normalize('NFKC')
    .toLowerCase()
    .replace(/[\s\p{P}\p{S}]+/gu, '')
}

/**
 * 홈의 질문 목록이 고를 수 있는 글 — FAQ 섹션이 가져간 글과, FAQ 섹션이 이미 보인 질문과 같은
 * 문장의 글을 뺀다. 같은 질문의 FAQ 글이 중복이라 FAQ 섹션에서 빠지면, id만 보고 거르는 예전
 * 규칙으로는 그 글이 질문 목록에 다시 나왔다.
 */
export function selectClusterContents<T extends Pick<ContentSummary, 'id' | 'faq_question' | 'title'>>(
  contents: readonly T[],
  faqEntries: readonly FaqEntry[],
): T[] {
  const entryIds = new Set(faqEntries.map((entry) => entry.id))
  const shownQuestions = new Set(faqEntries.map((entry) => faqQuestionKey(entry.question)).filter(Boolean))
  return contents.filter((content) => {
    if (entryIds.has(content.id)) return false
    const key = faqQuestionKey(content.faq_question || content.title || '')
    return !key || !shownQuestions.has(key)
  })
}
