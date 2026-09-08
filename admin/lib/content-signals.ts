// 콘텐츠 화면 하단의 읽기 전용 신호 표시값. 여기서는 판정하지 않고, 서버가 준
// 사실(고정 관측 슬롯 포함 여부·최근 측정값)을 문장으로만 바꾼다.

/** 고정 관측 슬롯에 들어간 질문인지 — 이 화면은 그 사실만 말한다. */
export function describeTrackingSet(target: { in_tracking_set: boolean }): string {
  return target.in_tracking_set ? '측정 대상' : '측정 제외'
}

/** 대상 월이 이번 달인 질문 + 월이 정해지지 않은 상시 질문. */
export function thisMonthTargets<T extends { target_month: string | null }>(
  targets: T[],
  year: number,
  month: number,
): T[] {
  const key = `${year}-${String(month).padStart(2, '0')}`
  return targets.filter((target) => target.target_month === null || target.target_month === key)
}

/** 측정한 적이 없으면 0%로 지어내지 않는다 — 없다고 말한다. */
export function latestMentionLabel(summary: {
  latest_sov_pct: number | null
  last_measured_at: string | null
}): string {
  if (summary.latest_sov_pct === null) return '측정 결과 없음'
  const pct = Number(summary.latest_sov_pct.toFixed(1))
  const measured = /^(\d{4})-(\d{2})-(\d{2})/.exec(summary.last_measured_at ?? '')
  if (!measured) return `언급률 ${pct}%`
  return `언급률 ${pct}% (${Number(measured[2])}/${Number(measured[3])} 측정)`
}
