/**
 * 대시보드(첫 화면) 숫자와 「오늘 할 일」 줄(2026-11-20 마케팅 요청). 계산은 모두 이미 있는 판정을 읽는다:
 * - 상태 수: 서버 3상태(`public_service_state`)와 병원 상태 — 화면이 따로 판정하지 않는다.
 * - 처리할 일: `nav-counts.operator_todo_groups`(문제·복구 큐 total, 일일 요약 「사람의 일」). 병원별
 *   예외 수의 합과는 다를 수 있다 — 같은 원인이 여러 병원에 걸치면 큐는 하나로 센다.
 * - 원장 보고 미전달: 병원 목록 위 상자와 같은 `/admin/operations/attention`(미생성 + 미전달).
 * - 공개 페이지 항목·준비 중 남은 조건·일시 정지는 할 일 숫자에 섞지 않고 따로 안내한다.
 */

import { ADMIN_COPY } from './admin-copy.ts'
import type { HospitalMonthMetrics } from './hospital-month-metrics.ts'
import type { NavCounts } from './nav-counts.ts'
import { isOnboardingHospital } from './hospital-list-filter.ts'
import { humanRemaining } from './hospital-states.ts'

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
  /** 보조 호출(원장 보고·이번 달 지표)이 아직 안 왔거나 실패하면 null — 0건과 다르다(「—」로 보인다). */
  readonly reportGapHospitals: number | null
  readonly leadTotal: number | null
  readonly leadNeedsAttention: number | null
  readonly monthPublic: number | null
  readonly monthPlanned: number | null
}

export function summarizeDashboard(
  hospitals: readonly DashboardHospital[],
  metrics: ReadonlyMap<string, HospitalMonthMetrics> | null,
  reports: DashboardReportGaps | null,
  counts: NavCounts | null,
): DashboardSummary {
  const active = hospitals.filter((h) => h.status !== 'PAUSED')
  const gapIds = new Set([...(reports?.missing ?? []), ...(reports?.undelivered ?? [])].map((row) => row.hospital_id))
  let monthPublic = 0
  let monthPlanned = 0
  for (const hospital of metrics ? active : []) {
    const row = metrics?.get(hospital.id)
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
    reportGapHospitals: reports ? gapIds.size : null,
    leadTotal: counts ? counts.lead_total : null,
    leadNeedsAttention: counts ? counts.lead_needs_attention : null,
    monthPublic: metrics ? monthPublic : null,
    monthPlanned: metrics ? monthPlanned : null,
  }
}

const COPY = ADMIN_COPY.dashboardScreen

type TodoKind = 'exception' | 'report' | 'visual' | 'preparing' | 'paused'

// 줄 종류마다 순서를 둔다 — 같은 주의 단계라도 원장 보고가 공개 페이지 항목·준비 중보다 먼저(위 8줄에서 가려지지 않게).
const KIND_ORDER: Record<TodoKind, number> = { exception: 0, report: 1, visual: 2, preparing: 3, paused: 4 }
const KIND_TONE: Record<TodoKind, TodoTone> = {
  exception: 'urgent', report: 'attention', visual: 'attention', preparing: 'attention', paused: 'info',
}

/** 오늘 할 일 — 병원별 예외(긴급) → 원장 보고 → 공개 페이지 항목 → 준비 중 남은 일(주의) → 일시 정지(정보). */
export function todoLines(
  hospitals: readonly DashboardHospital[],
  reports: DashboardReportGaps | null,
): TodoLine[] {
  const entries: { kind: TodoKind; line: TodoLine }[] = []
  const add = (kind: TodoKind, h: DashboardHospital, title: string, href: string, action: string) => {
    entries.push({ kind, line: { key: `${h.id}:${kind}`, hospitalId: h.id, hospitalName: h.name, tone: KIND_TONE[kind], title, href, action } })
  }
  const missing = new Set((reports?.missing ?? []).map((row) => row.hospital_id))
  const undelivered = new Set((reports?.undelivered ?? []).map((row) => row.hospital_id))
  for (const h of hospitals) {
    if (h.open_exception_count > 0) {
      add('exception', h, COPY.todoException.replace('{count}', String(h.open_exception_count)), `/hospitals/${h.id}`, COPY.todoExceptionAction)
    }
    if (missing.has(h.id) || undelivered.has(h.id)) {
      add('report', h, COPY.todoReport
        .replace('{month}', String(reports?.period_month ?? ''))
        .replace('{state}', missing.has(h.id) ? COPY.todoReportMissing : COPY.todoReportUndelivered),
      `/hospitals/${h.id}/reports`, COPY.todoReportAction)
    }
    const visual = h.visual_approval_missing?.length ?? 0
    if (h.status !== 'PAUSED' && visual > 0) {
      add('visual', h, COPY.todoVisual.replace('{count}', String(visual)), `/hospitals/${h.id}/info#info-brand`, COPY.todoVisualAction)
    }
    // 사람 몫 조건만 센다 — 「조건이 채워지면 자동 공개」처럼 시스템이 맡은 조건은 할 일이 아니다.
    const humanLeft = humanRemaining(h.public_service_state.remaining.filter((item): item is string => typeof item === 'string')).length
    if (isOnboardingHospital({ status: h.status as never }) && humanLeft > 0) {
      add('preparing', h, COPY.todoPreparing.replace('{count}', String(humanLeft)), `/hospitals/${h.id}`, COPY.todoStateAction)
    }
    if (h.status === 'PAUSED') {
      add('paused', h, COPY.todoPaused, `/hospitals/${h.id}`, COPY.todoStateAction)
    }
  }
  return entries
    .map((entry, index) => ({ ...entry, index }))
    .sort((a, b) => KIND_ORDER[a.kind] - KIND_ORDER[b.kind] || a.index - b.index)
    .map((entry) => entry.line)
}
