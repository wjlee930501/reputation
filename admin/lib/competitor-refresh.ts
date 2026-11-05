import { isRecord } from './type-guards.ts'

/**
 * 보고서 상세의 ‘경쟁 병원 반영’ — 서버 응답을 운영자 문구로 바꾸는 순수 함수.
 *
 * 경쟁 병원은 병원 정보 탭에서 등록한다(여기서는 입력받지 않는다). 버튼은 저장된 답변에 등록
 * 경쟁사의 판정을 보충한 뒤 **숫자는 그대로 두고 경쟁 현황만 다시 계산한 새 버전**을 만든다.
 * 새 버전은 다시 전달해야 원장님께 닿는다 — 그 경고를 어느 상태에서도 지우지 않는다.
 */
export type CompetitorReportRunView = {
  readonly runId: string
  readonly state: string
  readonly stage: string | null
  readonly reportVersion: number | null
  readonly reportId: string | null
}

export type CompetitorRefreshRunView = {
  readonly runId: string
  readonly state: string
  readonly stage: string | null
  readonly safeErrorCode: string | null
  readonly requestedAt: string | null
  readonly completedAt: string | null
  readonly judgedRecords: number | null
  readonly unresolvedRecords: number | null
  readonly pendingRecords: number | null
  readonly costBlocked: boolean
  readonly reportRun: CompetitorReportRunView | null
}

export type CompetitorRefreshStatus = {
  readonly registeredCompetitors: readonly string[]
  readonly recordsTotal: number
  readonly pendingRecords: number
  readonly pendingNames: readonly string[]
  readonly reportVersion: number | null
  readonly latestRun: CompetitorRefreshRunView | null
}

export const COMPETITOR_REDELIVER_WARNING =
  '새 버전은 다시 전달해야 원장님께 반영됩니다. 기존 보고서와 전달 기록은 그대로 보존됩니다.'

const ACTIVE_STATES = new Set(['REQUESTED', 'QUEUED', 'RUNNING'])

function text(value: unknown): string | null {
  return typeof value === 'string' && value.length > 0 ? value : null
}

function count(value: unknown): number | null {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0 ? value : null
}

function stringList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string' && item.length > 0) : []
}

function parseReportRun(value: unknown): CompetitorReportRunView | null {
  if (!isRecord(value)) return null
  const runId = text(value.run_id)
  const state = text(value.state)
  if (!runId || !state) return null
  return {
    runId,
    state,
    stage: text(value.stage),
    reportVersion: count(value.report_version),
    reportId: text(value.report_id),
  }
}

export function parseCompetitorRefreshRun(value: unknown): CompetitorRefreshRunView | null {
  if (!isRecord(value)) return null
  const runId = text(value.run_id)
  const state = text(value.state)
  if (!runId || !state) return null
  return {
    runId,
    state,
    stage: text(value.stage),
    safeErrorCode: text(value.safe_error_code),
    requestedAt: text(value.requested_at),
    completedAt: text(value.completed_at),
    judgedRecords: count(value.judged_records),
    unresolvedRecords: count(value.unresolved_records),
    pendingRecords: count(value.pending_records),
    costBlocked: value.cost_blocked === true,
    reportRun: parseReportRun(value.report_run),
  }
}

export function parseCompetitorRefreshStatus(payload: unknown): CompetitorRefreshStatus | null {
  if (!isRecord(payload)) return null
  return {
    registeredCompetitors: stringList(payload.registered_competitors),
    recordsTotal: count(payload.records_total) ?? 0,
    pendingRecords: count(payload.pending_records) ?? 0,
    pendingNames: stringList(payload.pending_names),
    reportVersion: count(payload.report_version),
    latestRun: parseCompetitorRefreshRun(payload.latest_run),
  }
}

/** 재판정 실행이나 그 자식 보고서 실행이 아직 돌고 있는지 — 화면이 5초마다 다시 읽는 조건. */
export function competitorRefreshIsActive(run: CompetitorRefreshRunView | null): boolean {
  if (!run) return false
  if (ACTIVE_STATES.has(run.state)) return true
  return run.state === 'SUCCEEDED' && (run.reportRun === null || ACTIVE_STATES.has(run.reportRun.state))
}

export type CompetitorRefreshPlan =
  | { readonly kind: 'register'; readonly message: string }
  | { readonly kind: 'ready'; readonly message: string }
  | { readonly kind: 'running'; readonly message: string }

/**
 * 버튼을 내줄지, 어떤 안내를 붙일지. 판정은 화면이 아니라 여기 한곳에서 한다.
 * - 등록 경쟁사가 없으면 병원 정보 탭으로 보낸다(이 화면에는 입력이 없다).
 * - 진행 중이면 같은 달에 두 번 보내지 않는다(서버도 409로 막는다).
 */
export function competitorRefreshPlan(status: CompetitorRefreshStatus): CompetitorRefreshPlan {
  if (status.registeredCompetitors.length === 0) {
    return {
      kind: 'register',
      message: '등록된 경쟁 병원이 없습니다. 병원 정보 탭에서 경쟁 병원을 등록한 뒤 여기서 반영해 주세요.',
    }
  }
  if (competitorRefreshIsActive(status.latestRun)) {
    return { kind: 'running', message: '이 달의 경쟁 병원 반영이 진행 중입니다. 끝나면 아래에 결과가 보입니다.' }
  }
  const names = status.registeredCompetitors.join(', ')
  const pending =
    status.pendingRecords > 0
      ? `저장된 AI 답변 ${status.recordsTotal}건 중 ${status.pendingRecords}건에 아직 이 경쟁 병원의 판정이 없어 먼저 보충합니다.`
      : `저장된 AI 답변 ${status.recordsTotal}건에 등록 경쟁 병원의 판정이 모두 있어 경쟁 현황만 다시 계산합니다.`
  return {
    kind: 'ready',
    message: `등록 경쟁 병원 ${status.registeredCompetitors.length}곳(${names}). ${pending} 측정 숫자는 바뀌지 않습니다.`,
  }
}

export type CompetitorRefreshProgress = {
  readonly label: string
  readonly detail: string
  readonly tone: 'progress' | 'done' | 'problem'
}

/** 최근 실행 한 건을 ‘무슨 일이 있었는지 · 결과 수’ 한 줄로. */
export function competitorRefreshProgress(run: CompetitorRefreshRunView | null): CompetitorRefreshProgress | null {
  if (!run) return null
  const counts =
    run.judgedRecords !== null || run.unresolvedRecords !== null
      ? `판정 보충 ${run.judgedRecords ?? 0}건 · 미확정 ${run.unresolvedRecords ?? 0}건`
      : '판정 결과 집계 전'
  if (ACTIVE_STATES.has(run.state)) {
    return { label: '경쟁 병원 판정을 보충하는 중', detail: '저장된 AI 답변에서 등록 경쟁 병원이 언급됐는지 확인하고 있습니다.', tone: 'progress' }
  }
  if (run.state === 'SUCCEEDED') {
    const report = run.reportRun
    if (!report || ACTIVE_STATES.has(report.state)) {
      return { label: '경쟁 현황을 다시 만드는 중', detail: `${counts}. 숫자는 그대로 두고 경쟁 현황만 다시 계산한 새 버전을 만들고 있습니다.`, tone: 'progress' }
    }
    if (report.state === 'SUCCEEDED') {
      const version = report.reportVersion ? ` 새 버전 v${report.reportVersion}이 준비됐습니다.` : ' 새 버전이 준비됐습니다.'
      return { label: '경쟁 병원 반영 완료', detail: `${counts}.${version} ${COMPETITOR_REDELIVER_WARNING}`, tone: 'done' }
    }
    if (report.state === 'PARTIAL' && report.reportVersion) {
      // 새 버전은 생겼지만 전달 조건(측정 완료·원장 PDF)이 아직이다 — '만들지 않았다'와 다르다.
      return {
        label: '새 버전은 만들어졌지만 전달 전 확인이 필요합니다',
        detail: `${counts}. 새 버전 v${report.reportVersion}이 생겼으나 전달 차단 사유가 남아 있습니다. 보고서 목록에서 새 버전을 열어 차단 사유를 확인해 주세요. ${COMPETITOR_REDELIVER_WARNING}`,
        tone: 'problem',
      }
    }
    return {
      label: '새 버전을 만들지 못했습니다',
      detail: `${counts}. 경쟁 현황만 바꾸는 조건(다른 숫자 보존·원장 PDF 검증)을 지키지 못해 새 버전을 만들지 않았습니다. 운영 센터에서 작업 기록을 확인해 주세요.`,
      tone: 'problem',
    }
  }
  if (run.costBlocked || run.safeErrorCode === 'COMPETITOR_REJUDGE_COST_BLOCKED') {
    return { label: '비용 한도로 보류됨', detail: '이번에는 판정을 보충하지 못했습니다. 한도가 열린 뒤 다시 실행해 주세요.', tone: 'problem' }
  }
  return { label: '경쟁 병원 판정을 끝내지 못했습니다', detail: `${counts}. 잠시 뒤 다시 실행하면 미확정으로 남은 답변만 다시 확인합니다.`, tone: 'problem' }
}

export function competitorRefreshIdempotencyKey(reportId: string, nonce: string): string {
  return `competitor-refresh:${reportId}:${nonce}`.replace(/[^A-Za-z0-9_.:-]/g, '-').slice(0, 255)
}
