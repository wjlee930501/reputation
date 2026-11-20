/**
 * 대시보드(첫 화면) 숫자와 「오늘 할 일」 줄(2026-11-20 마케팅 요청). 계산은 모두 이미 있는 판정을 읽는다:
 * - 상태 수: 서버 3상태(`public_service_state`)와 병원 상태 — 화면이 따로 판정하지 않는다.
 * - 처리할 일: `nav-counts.operator_todo_groups`(문제·복구 큐 total, 일일 요약 「사람의 일」). 병원별
 *   예외 수의 합과는 다를 수 있다 — 같은 원인이 여러 병원에 걸치면 큐는 하나로 센다.
 * - 원장 보고 미전달: 병원 목록 위 상자와 같은 `/admin/operations/attention`(미생성 + 미전달).
 * - 공개 페이지 항목·준비 중 남은 조건·일시 정지는 할 일 숫자에 섞지 않고 따로 안내한다.
 */

import type { HospitalMonthMetrics } from './hospital-month-metrics.ts'
import type { NavCounts } from './nav-counts.ts'
import { isOnboardingHospital } from './hospital-list-filter.ts'

export interface DashboardHospital {
  readonly id: string
  readonly name: string
  readonly status: string
  readonly open_exception_count: number
  readonly visual_approval_missing?: readonly string[] | null
  readonly public_service_state: { readonly kind: string; readonly remaining: readonly unknown[] }
}

export interface DashboardReportGaps {
  readonly period_month: number
  readonly missing: readonly { hospital_id: string }[]
  readonly undelivered: readonly { hospital_id: string }[]
}

export type TodoTone = 'urgent' | 'attention' | 'info'

export interface TodoLine {
  readonly key: string
  readonly hospitalId: string
  readonly hospitalName: string
  readonly tone: TodoTone
  readonly title: string
  readonly href: string
  readonly action: string
}

export interface DashboardSummary {
  readonly live: number
  readonly preparing: number
  readonly paused: number
  readonly total: number
  readonly visualMissingHospitals: number
  readonly operatorTodo: number | null
  readonly reportGapHospitals: number
  readonly leadTotal: number | null
  readonly leadNeedsAttention: number | null
  readonly monthPublic: number
  readonly monthPlanned: number
}

export function summarizeDashboard(
  hospitals: readonly DashboardHospital[],
  metrics: ReadonlyMap<string, HospitalMonthMetrics>,
  reports: DashboardReportGaps | null,
  counts: NavCounts | null,
): DashboardSummary {
  const active = hospitals.filter((h) => h.status !== 'PAUSED')
  const gapIds = new Set([...(reports?.missing ?? []), ...(reports?.undelivered ?? [])].map((row) => row.hospital_id))
  let monthPublic = 0
  let monthPlanned = 0
  for (const hospital of active) {
    const row = metrics.get(hospital.id)
    if (!row) continue
    monthPublic += row.public_count
    monthPlanned += Math.max(0, row.planned_total)
  }
  return {
    // 세 숫자는 서버 공개 상태(live·not_live·paused) 하나로 나눈다 — 합이 전체와 같다.
    // 도메인 확인 대기 병원도 기본 주소로 공개 중이면 「공개 중」이다.
    live: hospitals.filter((h) => h.public_service_state.kind === 'live').length,
    preparing: hospitals.filter((h) => h.public_service_state.kind === 'not_live').length,
    paused: hospitals.filter((h) => h.public_service_state.kind === 'paused').length,
    total: hospitals.length,
    visualMissingHospitals: active.filter((h) => (h.visual_approval_missing?.length ?? 0) > 0).length,
    operatorTodo: counts ? counts.operator_todo_groups : null,
    reportGapHospitals: gapIds.size,
    leadTotal: counts ? counts.lead_total : null,
    leadNeedsAttention: counts ? counts.lead_needs_attention : null,
    monthPublic,
    monthPlanned,
  }
}

const TONE_ORDER: Record<TodoTone, number> = { urgent: 0, attention: 1, info: 2 }

/** 오늘 할 일 — 병원별 예외(긴급) → 원장 보고 → 공개 페이지 항목·준비 중 남은 일(주의) → 일시 정지(정보). */
export function todoLines(
  hospitals: readonly DashboardHospital[],
  reports: DashboardReportGaps | null,
): TodoLine[] {
  const lines: TodoLine[] = []
  const missing = new Set((reports?.missing ?? []).map((row) => row.hospital_id))
  const undelivered = new Set((reports?.undelivered ?? []).map((row) => row.hospital_id))
  for (const h of hospitals) {
    if (h.open_exception_count > 0) {
      lines.push({ key: `${h.id}:exception`, hospitalId: h.id, hospitalName: h.name, tone: 'urgent',
        title: `예외 ${h.open_exception_count}건`, href: `/hospitals/${h.id}`, action: '원인 보기' })
    }
    if (missing.has(h.id) || undelivered.has(h.id)) {
      lines.push({ key: `${h.id}:report`, hospitalId: h.id, hospitalName: h.name, tone: 'attention',
        title: `${reports?.period_month ?? ''}월 원장 보고 ${missing.has(h.id) ? '미생성' : '미전달'}`,
        href: `/hospitals/${h.id}/reports`, action: '보고서 보기' })
    }
    if (h.status !== 'PAUSED' && (h.visual_approval_missing?.length ?? 0) > 0) {
      lines.push({ key: `${h.id}:visual`, hospitalId: h.id, hospitalName: h.name, tone: 'attention',
        title: `공개 페이지 항목 입력 필요 ${h.visual_approval_missing?.length}건`,
        href: `/hospitals/${h.id}/info#info-brand`, action: '병원 정보 입력' })
    }
    if (isOnboardingHospital({ status: h.status as never }) && h.public_service_state.remaining.length > 0) {
      lines.push({ key: `${h.id}:preparing`, hospitalId: h.id, hospitalName: h.name, tone: 'attention',
        title: `준비 중 · 남은 조건 ${h.public_service_state.remaining.length}개`,
        href: `/hospitals/${h.id}`, action: '상태 보기' })
    }
    if (h.status === 'PAUSED') {
      lines.push({ key: `${h.id}:paused`, hospitalId: h.id, hospitalName: h.name, tone: 'info',
        title: '일시 정지 중', href: `/hospitals/${h.id}`, action: '상태 보기' })
    }
  }
  return lines
    .map((line, index) => ({ line, index }))
    .sort((a, b) => TONE_ORDER[a.line.tone] - TONE_ORDER[b.line.tone] || a.index - b.index)
    .map((entry) => entry.line)
}
