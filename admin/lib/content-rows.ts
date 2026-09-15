// 월 표의 행 표시 — 상태 판정은 서버(row_state)가 하고, 여기서는 톤과 필터만 붙인다.
// 화면이 자기 규칙으로 상태를 다시 계산하면 admin만 "공개 중"이라고 말하는 글이 생긴다(H-01).

import type { ContentRowState, ContentRowStateKind } from '../types/index.ts'
import { isCarriedOver } from './content.ts'
import { SAFE_CAUSE_CODE_MESSAGES } from './operations-center.ts'

export type RowTone = 'good' | 'neutral' | 'warn' | 'paused'

export interface RowStateDescription {
  label: string
  detail: string | null
  tone: RowTone
  href: string | null
}

export const ROW_STATE_LABELS: Record<ContentRowStateKind, string> = {
  public: '공개 중',
  withheld: '공개 보류',
  scheduled: '예정',
  generating: '초안 생성 중',
  blocked: '차단',
  closed: '종료',
}

const ROW_STATE_TONES: Record<ContentRowStateKind, RowTone> = {
  public: 'good',
  withheld: 'warn',
  scheduled: 'neutral',
  generating: 'neutral',
  blocked: 'warn',
  closed: 'paused',
}

const IMMEDIATE_GENERATION_DETAIL =
  '저장 직후 생성을 요청했습니다 · 실패 시 새벽 스윕이 다시 시도합니다'
const NIGHTLY_GENERATION_DETAIL =
  '발행 전날 23:00 자동 생성 · 실패 시 새벽 스윕이 다시 시도합니다'

function isoDay(offsetDays: number, today: string): string {
  const base = new Date(`${today}T00:00:00Z`)
  if (Number.isNaN(base.getTime())) return ''
  base.setUTCDate(base.getUTCDate() + offsetDays)
  return base.toISOString().slice(0, 10)
}

/**
 * 생성 중인 슬롯이 실제로 언제 생성을 요청받았는지.
 *
 * 오늘·내일 슬롯은 발행 일정을 저장할 때 서버가 바로 대기열에 넣는다 — 그 슬롯에
 * "발행 전날 23:00"이라고 쓰면 이미 돌고 있는 일을 아직 시작도 안 한 것처럼 말한다.
 * 저장된 시도 실패 코드가 있으면 그 사유가 더 구체적이므로 먼저 보여 준다.
 */
export function generatingDetail(
  reason: string | null | undefined,
  scheduledDate: string | null | undefined,
  today: string = new Date().toISOString().slice(0, 10),
): string {
  const cleaned = reason?.trim() ?? ''
  if (cleaned) return SAFE_CAUSE_CODE_MESSAGES[cleaned] ?? cleaned
  const date = scheduledDate?.slice(0, 10) ?? ''
  const soon = date !== '' && (date <= today || date === isoDay(1, today))
  return soon ? IMMEDIATE_GENERATION_DETAIL : NIGHTLY_GENERATION_DETAIL
}

/** 행 하나의 표시값. 라벨은 서버가 준 것을 그대로 쓰고, 없을 때만 같은 표를 읽는다. */
export function describeRowState(
  rowState: ContentRowState,
  scheduledDate?: string | null,
): RowStateDescription {
  const label = rowState.label?.trim() || ROW_STATE_LABELS[rowState.kind]
  const detail = rowState.kind === 'generating'
    ? generatingDetail(rowState.reason, scheduledDate)
    : rowState.reason ?? rowState.link?.next_action ?? null
  return {
    label,
    detail,
    tone: ROW_STATE_TONES[rowState.kind],
    href: rowState.link?.href ?? null,
  }
}

export interface ContentRowItem {
  row_state: ContentRowState
  carried_over_from?: string | null
  status?: string
  post_publish_review_required?: boolean
  post_publish_reviewed_at?: string | null
  compliance?: { public_visibility?: { visible: boolean } }
}

/** 사람이 손대는 유일한 대상 — 공개 페이지에 실제로 있고 아직 확인하지 않은 표본. */
export function canConfirmSample(item: ContentRowItem): boolean {
  return (
    item.status === 'PUBLISHED' &&
    item.post_publish_review_required === true &&
    item.compliance?.public_visibility?.visible === true &&
    !item.post_publish_reviewed_at
  )
}

export type ContentRowFilter = 'all' | 'carried' | ContentRowStateKind

export const ROW_FILTERS: readonly ContentRowFilter[] = [
  'all',
  'carried',
  'public',
  'withheld',
  'scheduled',
  'generating',
  'blocked',
  'closed',
]

export const ROW_FILTER_LABELS: Record<ContentRowFilter, string> = {
  all: '전체',
  carried: '이월',
  ...ROW_STATE_LABELS,
}

export type RowSummary = Record<ContentRowStateKind | 'carried', number>

export function summarizeRows(items: ContentRowItem[]): RowSummary {
  const totals: RowSummary = {
    public: 0,
    withheld: 0,
    scheduled: 0,
    generating: 0,
    blocked: 0,
    closed: 0,
    carried: 0,
  }
  for (const item of items) {
    totals[item.row_state.kind]++
    if (isCarriedOver(item)) totals.carried++
  }
  return totals
}

/** 차단 카드의 힌트 — 갈 곳이 있을 때만 "운영 센터"라고 말한다.
 *
 * 링크 없는 차단은 자동 복구가 도는 중이라는 뜻이다. 그 상태에 "운영 센터에서 조치"라고
 * 쓰면 AE는 갈 데 없는 지시를 읽고 운영 센터에서 빈 큐를 본다. */
export function blockedHint(items: ContentRowItem[]): string {
  const routable = items.some(
    (item) => item.row_state.kind === 'blocked' && !!item.row_state.link?.href,
  )
  return routable ? '운영 센터에서 조치' : '자동 복구 대기'
}

export function matchesRowFilter(item: ContentRowItem, filter: ContentRowFilter): boolean {
  if (filter === 'all') return true
  if (filter === 'carried') return isCarriedOver(item)
  return item.row_state.kind === filter
}
