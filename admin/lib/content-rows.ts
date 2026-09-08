// 월 표의 행 표시 — 상태 판정은 서버(row_state)가 하고, 여기서는 톤과 필터만 붙인다.
// 화면이 자기 규칙으로 상태를 다시 계산하면 admin만 "공개 중"이라고 말하는 글이 생긴다(H-01).

import type { ContentRowState, ContentRowStateKind } from '../types/index.ts'
import { isCarriedOver } from './content.ts'

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

/** 행 하나의 표시값. 라벨은 서버가 준 것을 그대로 쓰고, 없을 때만 같은 표를 읽는다. */
export function describeRowState(rowState: ContentRowState): RowStateDescription {
  const label = rowState.label?.trim() || ROW_STATE_LABELS[rowState.kind]
  return {
    label,
    detail: rowState.reason ?? rowState.link?.next_action ?? null,
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

export function matchesRowFilter(item: ContentRowItem, filter: ContentRowFilter): boolean {
  if (filter === 'all') return true
  if (filter === 'carried') return isCarriedOver(item)
  return item.row_state.kind === filter
}
