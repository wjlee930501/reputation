/**
 * 병원 현황 화면(`/hospitals/{id}`)이 서버 판정을 문구로만 바꾸는 곳.
 *
 * 판정과 인가는 전부 `GET /admin/hospitals/{id}/overview` 한 번의 응답에 들어 있다.
 * 화면이 조건을 다시 세거나 행동 경로를 새로 지으면, 같은 병원이 헤더·목록·현황에서
 * 서로 다른 상태로 보이고 버튼이 서버 인가와 갈린다.
 */
import { ADMIN_COPY, describeMentionRate } from './admin-copy.ts'
import type { RemainingCondition } from './hospital-states.ts'
import type {
  HospitalOverviewException,
  HospitalOverviewMonth,
  OperationsAction,
} from '../types/index.ts'

export interface StatusStateCardValue {
  label: string
  remaining: readonly RemainingCondition[]
}

export interface SplitRemaining {
  human: RemainingCondition[]
  system: RemainingCondition[]
}

/**
 * 사람이 손대야 하는 조건과 자동으로 진행 중인 조건을 나눈다. 자동 진행 중인 일을
 * 할 일 목록에 섞으면 운영자가 손댈 수 없는 항목이 계속 남는다.
 *
 * 서버가 새 actor 값을 늘려도 조건이 화면에서 사라지지 않도록, human이 아닌 것은
 * 전부 시스템 몫으로 본다.
 */
export function splitRemaining(remaining: readonly RemainingCondition[]): SplitRemaining {
  return {
    human: remaining.filter((condition) => condition.actor === 'human'),
    system: remaining.filter((condition) => condition.actor !== 'human'),
  }
}

/** 카드 머리의 한 줄. 라벨은 서버 판정 그대로 쓰고 남은 조건만 세어 준다. */
export function stateCardCopy(card: StatusStateCardValue): { label: string; summary: string } {
  const { human, system } = splitRemaining(card.remaining)
  const parts: string[] = []
  if (human.length > 0) parts.push(`할 일 ${human.length}개`)
  if (system.length > 0) parts.push(`시스템 처리 중 ${system.length}개`)
  return { label: card.label, summary: parts.length > 0 ? parts.join(' · ') : '남은 조건 없음' }
}

function isoMonthDay(value: string): { month: number; day: number } | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(value)
  if (!match) return null
  return { month: Number(match[2]), day: Number(match[3]) }
}

/**
 * 언급률은 값만 보이면 지난달 수치가 오늘 수치가 된다 — 잰 날짜를 늘 함께 말한다.
 * 측정이 없으면 0%가 아니라 "측정 결과 없음"이다.
 */
function mentionRateLine(month: HospitalOverviewMonth): string {
  if (month.mention_rate === null) return describeMentionRate(null)
  const rate = `${ADMIN_COPY.aiMentionRate} ${describeMentionRate(month.mention_rate)}`
  const measured = month.mention_rate_measured_at ? isoMonthDay(month.mention_rate_measured_at) : null
  return measured ? `${rate} (${measured.month}/${measured.day} 측정)` : rate
}

/**
 * 이번 달 요약 줄들.
 *
 * 공개 수와 발행 수는 다르다(공개 API가 실제로 내보내는 글 / DB PUBLISHED). 보류가
 * 없으면 "0편" 줄을 만들지 않는다 — 아무 일도 없는 상태를 항목으로 만들지 않는다.
 */
export function monthSummaryLines(month: HospitalOverviewMonth): string[] {
  const lines = [
    `공개 ${month.public_count} / 발행 ${month.published_count} / 계획 ${month.planned_total}편`,
  ]
  if (month.withheld_count > 0) lines.push(`공개 보류 ${month.withheld_count}편`)
  lines.push(mentionRateLine(month))
  const next = isoMonthDay(month.next_report_date)
  lines.push(
    `다음 ${ADMIN_COPY.monthlyReport} ${next ? `${next.month}월 ${next.day}일` : month.next_report_date}`,
  )
  return lines
}

/** 버튼으로 그릴 행동의 순서. 운영자가 먼저 시도할 것부터 왼쪽에 둔다. */
const ACTION_ORDER: readonly string[] = [
  'RETRY_RUN',
  'RECOVER_INCIDENT',
  'ACK_INCIDENT',
  'ASSIGN_INCIDENT',
]

/** 서버가 `enabled: false`로 보낸 행동에 붙일 문구. */
export const ACTION_DISABLED_REASON = '권한 있는 담당자만'

export interface CardButtons {
  enabled: OperationsAction[]
  disabled: Array<{ action: OperationsAction; reason: string }>
}

/**
 * 카드가 그릴 버튼. `OPEN_INCIDENT`는 링크라 버튼에서 뺀다.
 * 지금 못 하는 행동도 버리지 않고 사유와 함께 돌려준다 — 버튼이 사라지면 왜 못 하는지
 * 알 수 없고, 그대로 두면 누르는 순간 403이 된다.
 */
export function cardButtons(exception: Pick<HospitalOverviewException, 'actions'>): CardButtons {
  const posts = (exception.actions ?? []).filter(
    (action) => action.kind !== 'OPEN_INCIDENT' && action.method === 'POST',
  )
  const rank = (action: OperationsAction): number => {
    const index = ACTION_ORDER.indexOf(action.kind)
    return index === -1 ? ACTION_ORDER.length : index
  }
  const sorted = [...posts].sort((left, right) => rank(left) - rank(right))
  return {
    enabled: sorted.filter((action) => action.enabled),
    disabled: sorted
      .filter((action) => !action.enabled)
      .map((action) => ({ action, reason: ACTION_DISABLED_REASON })),
  }
}

export interface MutationBodyInput {
  reason: string
  version: number | null
  ownerId?: string | null
  /**
   * 지금 걸려 있는 처리 기한 그대로. 담당 지정 요청은 이 값을 반드시 실어야 한다 —
   * 서버는 필드를 요구하고, 비운 채 보내면 기한이 지워진다.
   */
  slaDueAt?: string | null
}

/** 운영 센터가 쓰는 것과 같은 요청 본문. 화면마다 다른 몸통을 만들지 않는다. */
export function mutationBodyFor(
  action: OperationsAction,
  input: MutationBodyInput,
): Record<string, unknown> {
  const reason = input.reason.trim()
  if (action.kind === 'ASSIGN_INCIDENT') {
    return {
      expected_version: input.version,
      reason,
      owner_id: input.ownerId ?? null,
      sla_due_at: input.slaDueAt ?? null,
    }
  }
  if (action.kind === 'RETRY_RUN') return { reason }
  if (action.requires_version) return { expected_version: input.version, reason }
  return { reason }
}
