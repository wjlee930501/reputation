import type { ContentSummary } from './api.ts'
import { buildTreatmentSlug, type TreatmentLike } from './treatment-slug.ts'

/**
 * 진료 영역 상세에 관련 글이 아직 없을 때 무엇을 보여줄지 (P-A-2).
 *
 * 이전 화면은 "관련 의료 정보가 준비 중입니다" 한 줄과 전체 목록 링크로 끝났다.
 * 진료 항목만 등록하고 콘텐츠가 아직 붙지 않은 병원에서는 이 페이지가 사실상
 * 빈 문서였고, 그 상태로 sitemap과 llms.txt에 실려 답변 엔진에도 노출됐다.
 *
 * 이 병원에 실제로 있는 것만 고른다: 같은 병원의 다른 진료 영역과, 이미 발행된
 * 최신 글. 없는 것을 채우지 않으므로 두 목록 모두 비어 있을 수 있다.
 */

export interface TreatmentEmptyStatePaths<T extends TreatmentLike> {
  /** 같은 병원의 다른 진료 영역. 지금 보고 있는 항목은 뺀다. */
  siblings: T[]
  /** 아직 이 영역에 연결되지 않은, 이미 발행된 최신 글. */
  recentContents: ContentSummary[]
}

const SIBLING_LIMIT = 6
const RECENT_LIMIT = 3

function publishedAt(content: ContentSummary): number {
  const value = content.published_at || content.scheduled_date
  const parsed = value ? new Date(value).getTime() : Number.NaN
  return Number.isNaN(parsed) ? 0 : parsed
}

export function buildTreatmentEmptyStatePaths<T extends TreatmentLike>(input: {
  treatments: T[]
  currentTreatmentName: string
  contents: ContentSummary[]
}): TreatmentEmptyStatePaths<T> {
  const currentSlug = buildTreatmentSlug(input.currentTreatmentName)
  const siblings = input.treatments
    .filter((treatment) => {
      const slug = buildTreatmentSlug(treatment.name)
      return Boolean(slug) && slug !== currentSlug
    })
    .slice(0, SIBLING_LIMIT)

  const recentContents = [...input.contents]
    .sort((a, b) => publishedAt(b) - publishedAt(a))
    .slice(0, RECENT_LIMIT)

  return { siblings, recentContents }
}
