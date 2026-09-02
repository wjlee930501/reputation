export type DiagnosisSlots = {
  total: number
  used: number
  remaining: number
  soldOut: boolean
}

// 자리가 리셋되는 시각(KST) — backend SLOT_RESET_HOUR_KST(app/api/public/diagnosis.py)와
// 반드시 같은 값이어야 한다. 어긋나면 화면 문구("매일 오전 N시에 새 접수가 열립니다")가
// 실제 리셋 시각과 다른 값을 안내하게 된다. 값 자체는 두 소스가 각각 갖되(런타임 공유
// 불가), 이 상수를 문구 생성의 유일한 출처로 두어 문구가 조용히 드리프트하지 않게 한다.
export const DIAGNOSIS_SLOT_RESET_HOUR_KST = 8

export function diagnosisSlotResetCopy(hour: number = DIAGNOSIS_SLOT_RESET_HOUR_KST): string {
  return `매일 오전 ${hour}시에 새 접수가 열립니다.`
}

function isNonNegativeInteger(value: unknown): value is number {
  return Number.isInteger(value) && Number(value) >= 0
}

export function parseDiagnosisSlots(value: unknown): DiagnosisSlots | null {
  if (typeof value !== 'object' || value === null) return null

  const record = value as Record<string, unknown>
  const { total, used, remaining } = record
  if (
    !isNonNegativeInteger(total)
    || total === 0
    || !isNonNegativeInteger(used)
    || !isNonNegativeInteger(remaining)
    || used + remaining !== total
  ) {
    return null
  }

  return {
    total,
    used,
    remaining,
    soldOut: remaining === 0,
  }
}
