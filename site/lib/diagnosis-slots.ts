export type DiagnosisSlots = {
  total: number
  used: number
  remaining: number
  soldOut: boolean
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
