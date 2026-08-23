/**
 * 운영 기준 화면의 "다음 작업" 안내.
 *
 * 안내 배너는 ①②③④로 번호를 붙였는데, 화면의 실제 섹션은 1·2·3 세 개뿐이었다.
 * 그래서 마지막 안내가 "④ …"라고 말하는 순간 운영자가 찾아야 하는 ④번 섹션이
 * 화면에 존재하지 않는다. 게다가 그 ④번은 "차단 사유를 확인하세요"라고만 하고
 * 사유 자체는 오른쪽 패널 안쪽에 있어서, 어디를 봐야 하는지 알 수 없었다.
 *
 * 안내가 가리키는 번호를 실제 섹션 번호에서 받고, 차단 사유는 안내와 같은 자리에서
 * 함께 읽히게 한다.
 */

/** 화면 섹션 번호 — `StepLabel index`와 같은 값이어야 한다. */
export const ESSENCE_SECTIONS = {
  INPUT: 1,
  EXTRACT: 2,
  REVIEW: 3,
} as const

export type EssenceSection = (typeof ESSENCE_SECTIONS)[keyof typeof ESSENCE_SECTIONS]

export interface EssenceNextActionState {
  /** 사진을 제외한 근거 자료 수 */
  textSourceCount: number
  /** 근거 추출을 마친 자료 수 */
  processedTextCount: number
  hasSelectedDraft: boolean
  hasApproved: boolean
  /** 승인된 버전보다 새로운 검토 대기 초안이 선택돼 있다 */
  selectedIsReviewDraft: boolean
}

export interface EssenceNextAction {
  /** 운영자가 가야 할 화면 섹션 — 안내에 없는 번호를 부르지 않는다 */
  section: EssenceSection | null
  text: string
}

export function resolveEssenceNextAction(state: EssenceNextActionState): EssenceNextAction | null {
  if (state.textSourceCount === 0) {
    return { section: ESSENCE_SECTIONS.INPUT, text: '근거로 쓸 자료를 1개 이상 입력하세요.' }
  }
  if (state.processedTextCount === 0) {
    return { section: ESSENCE_SECTIONS.EXTRACT, text: '자료의 [근거 추출]을 실행하세요.' }
  }
  if (!state.hasSelectedDraft && !state.hasApproved) {
    return {
      section: ESSENCE_SECTIONS.EXTRACT,
      text: '처리한 자료를 선택하고 [선택한 자료로 초안 만들기]를 누르세요.',
    }
  }
  if (state.selectedIsReviewDraft) {
    return {
      section: ESSENCE_SECTIONS.REVIEW,
      text: 'AI 안전 검수가 보류한 최신 초안의 근거와 차단 사유를 확인하세요.',
    }
  }
  if (state.hasApproved) {
    return { section: null, text: '운영 중 — 새 자료를 추가하면 새 버전 초안을 만들 수 있습니다.' }
  }
  return null
}

/** 배너 한 줄 — 섹션이 있으면 그 번호를 앞에 붙인다. */
export function formatEssenceNextAction(action: EssenceNextAction): string {
  return action.section === null ? action.text : `${action.section}단계 — ${action.text}`
}

export interface UnsupportedGapLike {
  field?: string | null
  reason?: string | null
}

/**
 * 자동 검수가 초안을 보류한 이유. `field`가 자동 검수인 항목만 차단 사유다.
 *
 * `unsupported_gaps`는 서버가 자유롭게 채우는 JSON이라 화면은 형태를 신뢰하지 않는다.
 */
export function essenceAutoReviewBlockReasons(gaps: unknown): string[] {
  const rows = Array.isArray(gaps) ? gaps : []
  return rows
    .filter(
      (gap): gap is UnsupportedGapLike =>
        typeof gap === 'object' && gap !== null && (gap as UnsupportedGapLike).field === 'automatic_ai_review',
    )
    .map((gap) => (typeof gap.reason === 'string' ? gap.reason.trim() : ''))
    .filter((reason) => reason.length > 0)
}
