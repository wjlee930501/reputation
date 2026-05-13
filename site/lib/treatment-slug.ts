/**
 * Treatment-pillar URL slug helpers.
 *
 * 병원 프로파일의 treatments[]에는 slug 컬럼이 없다. Pillar 페이지의 URL을
 * 안정적으로 만들기 위해 name을 deterministic하게 변환한다.
 *
 * 한글은 그대로 두고 공백·중복 하이픈만 정리한다. (한국어 URL은 검색엔진과
 * 브라우저가 정상 처리하고, AI 답변에서 한국어 키워드 매칭이 더 강하다.)
 */
const FORBIDDEN_URL_CHARS = /[\s\/?#&=%+]+/g

export function buildTreatmentSlug(name: string | undefined | null): string {
  if (!name) return ''
  return name
    .normalize('NFKC')
    .trim()
    .replace(FORBIDDEN_URL_CHARS, '-')
    .replace(/-{2,}/g, '-')
    .replace(/^-+|-+$/g, '')
    .toLowerCase()
}

export interface TreatmentLike {
  name: string
  description?: string
}

export function findTreatmentBySlug<T extends TreatmentLike>(
  treatments: T[],
  slug: string,
): T | undefined {
  if (!slug) return undefined
  return treatments.find((t) => buildTreatmentSlug(t.name) === slug)
}

/**
 * 콘텐츠 1편이 어느 treatment pillar에 속하는지 추정.
 * 콘텐츠 title/meta/faq에 treatment name이 등장하면 매칭. 가장 먼저 매칭되는 1건만 사용한다.
 */
export function inferPillarTreatment<T extends TreatmentLike>(
  treatments: T[],
  content: {
    title?: string | null
    meta_description?: string | null
    faq_question?: string | null
  },
): T | undefined {
  const haystack = `${content.title ?? ''} ${content.meta_description ?? ''} ${content.faq_question ?? ''}`
  if (!haystack.trim()) return undefined
  const lowered = haystack.toLowerCase()
  for (const t of treatments) {
    const needle = t.name?.trim().toLowerCase()
    if (needle && lowered.includes(needle)) return t
  }
  // Treatment 한자/공백 차이 보정: "허리디스크 치료" vs "디스크"
  for (const t of treatments) {
    const stem = t.name?.replace(/[\s수술치료시술검사진료]/g, '').trim().toLowerCase()
    if (stem && stem.length >= 2 && lowered.includes(stem)) return t
  }
  return undefined
}
