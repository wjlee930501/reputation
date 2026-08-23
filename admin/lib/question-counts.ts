/**
 * 환자 질문 개수의 단일 어휘.
 *
 * 같은 병원에서 세 화면이 서로 다른 숫자를 서로 다른 이름으로 보여줬다.
 * 대시보드는 "측정 질문표 54개 · 운영 중 53개 · 측정용 질문 15개", 질문 화면은
 * "운영 중 / 운영 일시 정지 / 보관", 추이 그래프는 "측정한 환자 질문 15개"였다.
 * 세 숫자는 각각 질문 주제(AIQueryTarget), 질문 문구(variant), 측정표에 연결된
 * 문구(QueryMatrix)를 세고 있었지만 이름만 보면 무엇이 무엇인지 알 수 없었고,
 * "측정한"이라고 적힌 숫자는 실제로 한 번도 측정되지 않은 문구까지 포함했다.
 *
 * 그래서 세는 대상마다 하나의 이름을 정하고, 숫자가 서로 맞아떨어지게 한다.
 * 측정된 문구 + 측정 대기 문구 = 측정표에 연결된 문구.
 */

export interface QuestionTargetLike {
  status: string
  summary?: {
    variant_count?: number
    active_variant_count?: number
    linked_query_matrix_count?: number
    latest_sov_pct?: number | null
  } | null
}

/** `/sov/queries` 한 행 — 측정표에 연결된 질문 문구 하나. */
export interface MeasuredQuestionRowLike {
  total_count?: number
}

/**
 * 화면마다 다른 말로 부르던 것을 한 이름으로 고정한다.
 *
 * `topics`는 운영 단위(질문 주제)이고 `phrases`는 실제로 AI에 물어보는 문장이다.
 */
export const QUESTION_COUNT_LABELS = {
  topicsOperating: '운영 중 질문 주제',
  topicsPaused: '운영 일시 정지',
  topicsArchived: '보관',
  phrasesLinked: '측정표에 연결된 질문 문구',
  phrasesMeasured: '측정된 질문 문구',
  phrasesWaiting: '측정 대기 질문 문구',
} as const

export interface QuestionCountSummary
  extends Record<'topicsOperating' | 'topicsPaused' | 'topicsArchived', number> {
  /** 보관되지 않은 질문 주제 — 운영 중 + 일시 정지 */
  topicsTracked: number
  /** 측정표에 연결된 질문 문구 수 */
  phrasesLinked: number
  /** 성공 측정이 한 번이라도 있는 질문 문구 수 */
  phrasesMeasured: number
  /** 측정표에 있으나 아직 측정 결과가 없는 질문 문구 수 */
  phrasesWaiting: number
}

export function summarizeQuestionCounts(
  targets: QuestionTargetLike[],
  measuredRows: MeasuredQuestionRowLike[],
): QuestionCountSummary {
  const rows = Array.isArray(targets) ? targets : []
  const topicsOperating = rows.filter((target) => target.status === 'ACTIVE').length
  const topicsPaused = rows.filter((target) => target.status === 'PAUSED').length
  const topicsArchived = rows.filter((target) => target.status === 'ARCHIVED').length

  const phraseRows = Array.isArray(measuredRows) ? measuredRows : []
  const phrasesLinked = phraseRows.length
  // total_count는 언급률 분모에 들어간 측정 수다. 0이면 측정 결과가 아직 없다는 뜻이므로
  // "측정한 질문"으로 세면 안 된다 — 이게 대시보드가 15개를 잘못 부르던 지점이다.
  const phrasesMeasured = phraseRows.filter((row) => (row.total_count ?? 0) > 0).length

  return {
    topicsOperating,
    topicsPaused,
    topicsArchived,
    topicsTracked: topicsOperating + topicsPaused,
    phrasesLinked,
    phrasesMeasured,
    phrasesWaiting: phrasesLinked - phrasesMeasured,
  }
}

/** 질문 주제 카드의 보조 문구 — 대시보드와 질문 화면이 같은 문장을 쓴다. */
export function describeQuestionPhraseCounts(summary: QuestionCountSummary): string {
  if (summary.phrasesLinked === 0) {
    return '측정표에 연결된 질문 문구가 없습니다'
  }
  if (summary.phrasesMeasured === 0) {
    return `${QUESTION_COUNT_LABELS.phrasesWaiting} ${summary.phrasesWaiting}개 · 아직 측정 전`
  }
  if (summary.phrasesWaiting === 0) {
    return `${QUESTION_COUNT_LABELS.phrasesMeasured} ${summary.phrasesMeasured}개`
  }
  return `${QUESTION_COUNT_LABELS.phrasesMeasured} ${summary.phrasesMeasured}개 · ${QUESTION_COUNT_LABELS.phrasesWaiting} ${summary.phrasesWaiting}개`
}
