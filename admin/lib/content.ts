// 전월 이월 콘텐츠 처리 헬퍼.
// 반려된 슬롯이 월 경계를 넘어 이월되면 backend가 carried_over_from(원래 예정일)을 내려준다.
// 이월 슬롯은 다음 달에 가장 먼저 처리해야 하므로 목록 최상단으로 끌어올린다.

export interface CarriedOverItem {
  carried_over_from?: string | null
  status?: string
}

export function isCarriedOver(item: CarriedOverItem): boolean {
  return Boolean(item.carried_over_from)
}

/** 이월 슬롯을 앞으로, 나머지는 기존 순서 그대로 유지한다 (안정 정렬). */
export function sortCarriedOverFirst<T extends CarriedOverItem>(items: T[]): T[] {
  const carried: T[] = []
  const rest: T[] = []
  for (const item of items) {
    if (isCarriedOver(item)) carried.push(item)
    else rest.push(item)
  }
  return [...carried, ...rest]
}

export function countCarriedOver(items: CarriedOverItem[]): number {
  return items.filter(isCarriedOver).length
}

/** 아직 발행되지 않은 이월 슬롯 수 — 대시보드 우선 처리 알림 기준. */
export function countUnpublishedCarriedOver(items: CarriedOverItem[]): number {
  return items.filter((item) => isCarriedOver(item) && item.status !== 'PUBLISHED').length
}
