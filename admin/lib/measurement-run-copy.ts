/**
 * 측정 실행 로그의 성공/실패 문구.
 *
 * 화면은 실패율 옆에 "AI 언급률 계산에서 제외"라고만 적어서, 실행 하나가 통째로
 * 언급률에서 빠진다고 읽혔다. 실제 계산(`sov_engine.record_is_confirmed`)은 측정
 * **한 건씩** 판단한다 — 같은 실행 안에서 성공한 측정은 분모에 그대로 들어가고,
 * 실패한 측정과 판정이 확정되지 않은 측정만 빠진다. 그래서 실패가 섞인 실행도
 * 성공분은 언급률에 반영된다.
 *
 * 문구와 실제 동작이 어긋나면 운영자는 실패가 섞인 달의 언급률을 신뢰하지 않거나,
 * 반대로 실패를 무해하다고 넘긴다. 두 판단 모두 원장 보고를 틀리게 만든다.
 */

export interface MeasurementRunCounts {
  query_count: number
  success_count: number
  failure_count: number
  failure_rate: number | null
}

/** 언급률 분모에서 빠지는 조건 — 백엔드 `record_is_confirmed`와 같은 말을 쓴다. */
export const MENTION_RATE_EXCLUSION_COPY =
  '성공한 측정은 AI 언급률에 그대로 반영되고, 실패한 측정과 판정이 확정되지 않은 측정만 분모에서 빠집니다. 아래 성공·실패 집계는 측정 안정성을 보는 별도 지표입니다.'

/** 누적 실패 알림 — 실패가 실행 전체를 무효로 만들지 않는다는 점을 분명히 한다. */
export const MENTION_RATE_FAILURE_ALERT_COPY =
  '실패한 측정만 언급률 분모에서 빠지고, 같은 실행에서 성공한 측정은 언급률에 반영됩니다.'

/**
 * 실행 한 건의 실패율 아래에 붙는 문구.
 *
 * 실행이 통째로 제외된다고 말하지 않고, 그 실행에서 몇 건이 반영되고 몇 건이
 * 빠지는지를 센다.
 */
export function describeMeasurementRunMentionRateImpact(run: MeasurementRunCounts): string {
  const queryCount = Math.max(0, run.query_count ?? 0)
  const successCount = Math.max(0, run.success_count ?? 0)
  const failureCount = Math.max(0, run.failure_count ?? 0)

  if (queryCount === 0) {
    return '측정 건이 없어 실패율을 산출할 수 없습니다'
  }

  const failureRate =
    run.failure_rate !== null && run.failure_rate !== undefined
      ? run.failure_rate
      : (failureCount / queryCount) * 100
  const rateText = `실패율 ${failureRate.toFixed(1)}%`

  if (failureCount === 0) {
    return `${rateText} · 성공 ${successCount}건이 모두 AI 언급률에 반영됩니다`
  }
  if (successCount === 0) {
    return `${rateText} · 성공 측정이 없어 이 실행은 AI 언급률에 반영되지 않습니다`
  }
  return `${rateText} · 실패 ${failureCount}건만 분모에서 빠지고 성공 ${successCount}건은 AI 언급률에 반영됩니다`
}
