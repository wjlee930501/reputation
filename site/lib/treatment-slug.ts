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

/**
 * Next.js App Router는 dynamic route segment의 non-ASCII 문자를 URL-decoded
 * 형태로 전달하지 않는다(`params.treatmentSlug`이 `%ED%83%88%EC%9E%A5-...`).
 * 양쪽 형태 모두 매칭되도록 decoded 비교도 시도한다.
 */
export function findTreatmentBySlug<T extends TreatmentLike>(
  treatments: T[],
  slug: string,
): T | undefined {
  if (!slug) return undefined
  let decoded = slug
  try {
    decoded = decodeURIComponent(slug)
  } catch {
    // malformed percent-encoding — raw slug로 fallback
  }
  return treatments.find((t) => {
    const built = buildTreatmentSlug(t.name)
    return built === slug || built === decoded
  })
}

/**
 * params.treatmentSlug를 사람이 읽을 수 있는 (디코딩된) 형태로 정규화.
 * canonical URL, sitemap, JSON-LD에서 percent-encoded와 decoded 형태가
 * 섞이지 않도록 페이지 안에서 한 번에 통일하는 용도.
 */
export function normalizeTreatmentSlug(slug: string): string {
  if (!slug) return ''
  try {
    return decodeURIComponent(slug)
  } catch {
    return slug
  }
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
    query_target_treatment?: string | null
  },
): T | undefined {
  // 운영자가 승인한 query-target linkage가 있으면 제목/요약의 우연한 문자열보다 우선한다.
  const linkedTreatment = content.query_target_treatment?.trim().toLowerCase()
  if (linkedTreatment) {
    return treatments.find(
      (treatment) => treatment.name?.trim().toLowerCase() === linkedTreatment,
    )
  }

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
