import type {
  OperationsAction,
  OperationsIncidentDetail,
  OperationsQueueParam,
  OperationsQueueResponse,
  OperationsQueueRow,
  OperationsRunState,
  OperationsSlackState,
} from '../types/index.ts'

export interface OperationsQuery {
  readonly queue: OperationsQueueParam
  readonly owner: string
  readonly status: string
  readonly severity: string
  readonly sla: string
  readonly q: string
  readonly detail: string
  readonly page: number
}
export type OperationsQueryPatch = Readonly<
  Partial<Record<keyof OperationsQuery, string | number | null>>
>

export type QueueView = 'loading' | 'error' | 'empty' | 'ready'
export type FocusTarget = 'current-action' | 'queue-heading'

export interface OperationsConflict {
  readonly message: string
  readonly refetchPath: string | null
  readonly currentVersion: number | null
  readonly currentState: string | null
  readonly focusTarget: FocusTarget
}
const QUEUES: readonly OperationsQueueParam[] = ['onboarding', 'today', 'reports', 'incidents']
const FILTER_KEYS = ['queue', 'owner', 'status', 'severity', 'sla', 'q', 'detail', 'page'] as const

function bounded(value: string | null, max: number): string {
  return value?.trim().slice(0, max) ?? ''
}
function queueValue(value: string | null): OperationsQueueParam {
  const normalized = value?.toLowerCase()
  return QUEUES.find((queue) => queue === normalized) ?? 'today'
}
export function readOperationsQuery(source: URLSearchParams): OperationsQuery {
  const pageValue = Number.parseInt(source.get('page') ?? '', 10)
  return {
    queue: queueValue(source.get('queue')),
    owner: bounded(source.get('owner'), 120),
    status: bounded(source.get('status'), 60).toUpperCase(),
    severity: bounded(source.get('severity'), 20).toUpperCase(),
    sla: bounded(source.get('sla'), 20).toUpperCase(),
    q: bounded(source.get('q'), 120),
    detail: bounded(source.get('detail'), 160),
    page: Number.isSafeInteger(pageValue) && pageValue > 1 ? pageValue : 1,
  }
}

export function canonicalizeOperationsQuery(source: URLSearchParams): URLSearchParams {
  const query = readOperationsQuery(source)
  const result = new URLSearchParams()
  result.set('queue', query.queue)
  if (query.owner) result.set('owner', query.owner)
  if (query.status) result.set('status', query.status)
  if (query.severity) result.set('severity', query.severity)
  if (query.sla) result.set('sla', query.sla)
  if (query.q) result.set('q', query.q)
  if (query.detail) result.set('detail', query.detail)
  if (query.page > 1) result.set('page', String(query.page))
  return result
}

export function updateOperationsQuery(
  source: URLSearchParams,
  patch: OperationsQueryPatch,
): URLSearchParams {
  const next = canonicalizeOperationsQuery(source)
  for (const key of FILTER_KEYS) {
    if (!(key in patch)) continue
    const value = patch[key]
    if (value === null || value === '') next.delete(key)
    else if (value !== undefined) next.set(key, String(value))
  }
  const changedQueue = patch.queue !== undefined
  const changedFilter = ['owner', 'status', 'severity', 'sla', 'q'].some((key) => key in patch)
  if (changedQueue && patch.detail === undefined) next.delete('detail')
  if (changedQueue || changedFilter) next.delete('page')
  return canonicalizeOperationsQuery(next)
}

function actionPriority(item: OperationsQueueRow): number {
  if (item.sla_state === 'OVERDUE') return 0
  if (item.severity === 'CRITICAL' || item.severity === 'HIGH') return 1
  if (item.slack?.state === 'FAILED' || item.slack?.state === 'HOLD') return 2
  if (item.retry || item.status === 'RETRYING') return 3
  return 4
}

export function selectCurrentAction(
  items: readonly OperationsQueueRow[],
): OperationsQueueRow | null {
  return items.reduce<OperationsQueueRow | null>((selected, item) => {
    if (selected === null) return item
    return actionPriority(item) < actionPriority(selected) ? item : selected
  }, null)
}
export function runStateLabel(state: OperationsRunState): string {
  switch (state) {
    case 'REQUESTED': return '요청 접수'
    case 'QUEUED': return '대기 중'
    case 'RUNNING': return '실행 중'
    case 'SUCCEEDED': return '완료'
    case 'PARTIAL': return '일부 완료'
    case 'FAILED': return '실패'
    case 'CANCELLED': return '취소'
  }
}

export function slackStateLabel(state: OperationsSlackState): string {
  switch (state) {
    case 'PENDING': return '발송 대기'
    case 'SENDING': return '발송 중'
    case 'RETRYING': return '전송 재시도 대기'
    case 'HOLD': return '전송 결과 확인 필요'
    case 'SENT': return '발송 완료'
    case 'FAILED': return 'Slack 전달 실패'
  }
}
export function operationStatusLabel(status: string): string {
  switch (status) {
    case 'ONBOARDING': return '온보딩 진행 중'
    case 'ANALYZING': return 'AI 진단 분석 중'
    case 'BUILDING': return '콘텐츠 허브 준비 중'
    case 'PENDING_DOMAIN': return '도메인 확인 대기'
    case 'ACTIVE': return '운영 중'
    case 'PAUSED': return '운영 일시 정지'
    case 'PUBLISH_DUE': return '오늘 발행 예정'
    case 'REVIEW_PENDING': return '발행 후 확인 대기'
    case 'OVERDUE_REVIEW': return '발행 후 확인 기한 지남'
    case 'MISSING': return '지난달 보고서 미생성'
    case 'DELIVERY_PENDING': return '원장 전달 검수 대기'
    case 'OPEN': return '처리 필요'
    case 'RETRYING': return '복구 재시도 중'
    case 'RECOVERED': return '복구 확인됨'
    case 'ACKNOWLEDGED': return '확인 완료'
    default: return '상태 확인 필요'
  }
}
export function historyEventLabel(event: string): string {
  switch (event) {
    case 'OPENED': return '사건 등록'
    case 'OCCURRED': return '문제 발생'
    case 'RETRYING': return '복구 재시도 시작'
    case 'RECOVERED': return '복구 확인'
    case 'ACKNOWLEDGED': return '운영자 확인 완료'
    case 'REPORT_READY': return '보고서 생성'
    case 'PUBLISHED': return '콘텐츠 발행'
    default: return '운영 기록'
  }
}
export function buildDevelopmentSupportSummary(
  detail: OperationsIncidentDetail,
  origin: string,
): string {
  const row = detail.incident
  const query = new URLSearchParams({
    queue: row.queue.toLowerCase(),
    detail: row.id,
  })
  const recentAt = row.history.at(-1)?.at ?? row.occurred_at
  const code = detail.run?.safe_error_code ?? row.slack?.safe_error_code ?? '기록되지 않음'
  const description = row.safe_cause
    ?? detail.run?.safe_error_message
    ?? row.slack?.safe_error_message
    ?? '원인 설명이 기록되지 않았습니다. 아래 상세 URL과 함께 개발팀에 전달해 주세요.'
  return [
    '[운영 센터 개발팀 문의]',
    `병원: ${row.customer.name}`,
    `현상: ${operationStatusLabel(row.status)}`,
    `고객 영향: ${row.impact}`,
    `오류 식별자(개발팀용): ${code}`,
    `안전한 원인 설명: ${description}`,
    `발생 시각: ${row.occurred_at}`,
    `최근 시각: ${recentAt}`,
    `상세 URL: ${origin}/operations?${query.toString()}`,
  ].join('\n')
}
export function shouldPollRun(state: OperationsRunState): boolean {
  switch (state) {
    case 'REQUESTED':
    case 'QUEUED':
    case 'RUNNING': return true
    case 'SUCCEEDED':
    case 'PARTIAL':
    case 'FAILED':
    case 'CANCELLED': return false
  }
}
export function shouldAutoRetrySlack(state: OperationsSlackState): boolean {
  switch (state) {
    case 'RETRYING': return true
    case 'PENDING':
    case 'SENDING':
    case 'HOLD':
    case 'SENT':
    case 'FAILED': return false
  }
}

export function createUserActionKey(kind: string, targetId: string, nonce: string): string {
  return `admin:${kind}:${targetId}:${nonce}`.slice(0, 255)
}

export function enabledPostAction(action: OperationsAction | null): OperationsAction | null {
  return action?.enabled && action.method === 'POST' ? action : null
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export function interpretOperationsConflict(detail: unknown): OperationsConflict {
  const record = isRecord(detail) ? detail : {}
  return {
    message: '다른 운영자가 먼저 변경했습니다. 최신 상태로 갱신했습니다. 다시 확인해 주세요.',
    refetchPath: typeof record.refetch_path === 'string' ? record.refetch_path : null,
    currentVersion: typeof record.current_version === 'number' ? record.current_version : null,
    currentState: typeof record.current_state === 'string' ? record.current_state : null,
    focusTarget: 'current-action',
  }
}

export function deriveQueueView(
  page: OperationsQueueResponse | null,
  error: string,
  loading: boolean,
): QueueView {
  if (loading && page === null) return 'loading'
  if (error && page === null) return 'error'
  if (page?.items.length === 0) return 'empty'
  return 'ready'
}
