import { getApiBase } from './config.ts'

/**
 * 오늘 남은 무료 진단 자리 — 랜딩 히어로의 선착순 표시용.
 *
 * **읽지 못하면 숫자를 보여주지 않는다.** `app/api/diagnosis/slots/route.ts`가 세운 규칙과
 * 같다: 추측값을 넣으면 "실제 카운터"라는 약속이 깨지고, 마감인데 "자리 있음"으로 보이는
 * 것이 가장 나쁘다. 그래서 실패는 `null`이고, 화면은 숫자 없는 문구로 내려간다.
 *
 * 60초 재검증을 쓴다. 랜딩은 정적으로 서비스되는 게 맞고, 하루 20건 규모에서 1분 지연은
 * "남은 자리"의 의미를 훼손하지 않는다. 접수 화면(`/ai-diagnosis`)은 `no-store`로 실시간을
 * 쓰므로, 실제 마감 판정은 언제나 그쪽에서 일어난다.
 */
export type SlotStatus = {
  date: string
  total: number
  used: number
  remaining: number
}

const REVALIDATE_SECONDS = 60

function isNonNegativeInt(value: unknown): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0
}

/** 응답이 조금이라도 이상하면 버린다 — 반쯤 맞는 카운터는 없는 것보다 나쁘다. */
export function parseSlotStatus(payload: unknown): SlotStatus | null {
  if (typeof payload !== 'object' || payload === null) return null
  const row = payload as Record<string, unknown>
  if (typeof row.date !== 'string' || row.date.length === 0) return null
  if (!isNonNegativeInt(row.total) || !isNonNegativeInt(row.used) || !isNonNegativeInt(row.remaining)) {
    return null
  }
  if (row.total === 0) return null
  // used + remaining이 total과 어긋나면 우리가 이해하지 못하는 응답이다.
  if (row.used + row.remaining !== row.total) return null
  return { date: row.date, total: row.total, used: row.used, remaining: row.remaining }
}

export type SlotTone = 'is-open' | 'is-closed' | 'is-unknown'

/**
 * 선착순 문구를 결정한다 — **숫자를 만들지 않는 유일한 지점.**
 *
 * 카운터를 읽었으면 실제 남은 수를, 마감이면 마감을, 못 읽었으면 숫자 없는 문구를 쓴다.
 * 여기서 기본값으로 20을 넣으면 "실제 카운터"라는 약속이 조용히 깨진다.
 */
export function resolveSlotState(
  slots: SlotStatus | null,
  copy: { open: string; closed: string; fallback: string },
): { text: string; tone: SlotTone } {
  if (slots === null) return { text: copy.fallback, tone: 'is-unknown' }
  if (slots.remaining <= 0) return { text: copy.closed, tone: 'is-closed' }
  return {
    text: copy.open.replace('{remaining}', String(slots.remaining)),
    tone: 'is-open',
  }
}

export async function fetchTodaySlots(): Promise<SlotStatus | null> {
  try {
    const res = await fetch(`${getApiBase()}/diagnosis/slots`, {
      next: { revalidate: REVALIDATE_SECONDS },
    })
    if (!res.ok) return null
    return parseSlotStatus(await res.json())
  } catch {
    return null
  }
}
