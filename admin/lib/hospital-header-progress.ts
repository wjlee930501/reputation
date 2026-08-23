/**
 * 병원 상세 헤더의 진행 상태 요약.
 *
 * 헤더는 `lg`(1024px)부터 데스크톱 배치로 바뀌는데, 오른쪽에 다섯 개의 진행 표시를
 * 전부 펼친다("필수 병원 정보: 완료" 다섯 줄). `max-w-xl`(576px) 안에서 다섯 칩이
 * 두세 줄로 접히고, 왼쪽의 병원명·상태·요금제·공개 주소·마지막 확인 시각까지 겹쳐서
 * 1024~1280px 구간에서는 헤더가 화면 높이를 크게 차지한다 — 모바일은 `<select>`
 * 하나로 접히는데 그 바로 위 폭에서만 가장 커진다.
 *
 * 그래서 이 폭에서는 한 줄 요약으로 접고, 폭이 실제로 있는 `xl`부터 다섯 개를 펼친다.
 * 요약은 몇 개가 남았는지와 무엇이 남았는지를 함께 말한다 — 접었다고 정보를 잃지 않는다.
 */

export interface HeaderProgressHospital {
  profile_complete?: boolean | null
  v0_report_done?: boolean | null
  site_built?: boolean | null
  schedule_set?: boolean | null
  site_live?: boolean | null
}

export interface HeaderProgressItem {
  label: string
  done: boolean
}

export interface HeaderProgressSummary {
  items: HeaderProgressItem[]
  doneCount: number
  total: number
  /** 접힌 폭에서 보여줄 한 줄 */
  label: string
  /** 남은 항목 이름 — 접혀도 무엇이 남았는지 알 수 있게 한다 */
  pendingLabels: string[]
}

export function summarizeHeaderProgress(
  hospital: HeaderProgressHospital | null,
): HeaderProgressSummary {
  const items: HeaderProgressItem[] = [
    { label: '필수 병원 정보', done: Boolean(hospital?.profile_complete) },
    { label: '초기 진단 리포트', done: Boolean(hospital?.v0_report_done) },
    { label: '콘텐츠 허브 준비', done: Boolean(hospital?.site_built) },
    { label: '스케줄 설정', done: Boolean(hospital?.schedule_set) },
    { label: '병원 정보 허브', done: Boolean(hospital?.site_live) },
  ]
  const doneCount = items.filter((item) => item.done).length
  const pendingLabels = items.filter((item) => !item.done).map((item) => item.label)

  return {
    items,
    doneCount,
    total: items.length,
    pendingLabels,
    label:
      pendingLabels.length === 0
        ? `운영 준비 ${doneCount}/${items.length} 완료`
        : `운영 준비 ${doneCount}/${items.length} · 남은 항목 ${pendingLabels.join(', ')}`,
  }
}
