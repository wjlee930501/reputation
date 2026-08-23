import {
  STATUS_LABELS,
  type OperationsAction,
  type OperationsIncidentDetail,
  type OperationsQueueParam,
  type OperationsQueueResponse,
  type OperationsQueueRow,
  type OperationsRunState,
  type OperationsSlackState,
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
export type OperationsMutationKind =
  | 'RETRY_RUN'
  | 'RECOVER_INCIDENT'
  | 'ACK_INCIDENT'
  | 'POST_ACTION'

export interface OperationsMutationDescriptor {
  readonly kind: OperationsMutationKind
  readonly path: string
  readonly targetId: string
  readonly version: number | null
  readonly reason: string
  readonly label: string
  readonly requiresIdempotencyKey: boolean
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
  const hospitalStatus = STATUS_LABELS[status]
  if (hospitalStatus) return hospitalStatus.label
  switch (status) {
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
const UNKNOWN_SAFE_CAUSE = '원인 설명을 확인할 수 없습니다. 아래 조치가 실패하면 개발팀 문의용 정보를 복사해 주세요.'

/**
 * 오류 식별자를 운영자가 읽을 수 있는 원인 설명으로 바꾼다.
 *
 * 백엔드는 사건을 열 때 한국어 설명(`safe_error_message`)을 함께 남기는 경로도 있지만,
 * 코드만 남기는 경로도 많다(작업 실행 `safe_error_code`, Slack 전달 실패 등). 그때
 * 화면은 아는 코드가 여섯 개뿐이어서 나머지를 전부 "원인 설명을 확인할 수 없습니다"로
 * 덮었다 — 서버는 원인을 알고 있는데 운영자만 모르는 상태였고, 그 문구는 조치가 아니라
 * 개발팀 문의를 가리키므로 처리 가능한 일이 문의로 흘러갔다.
 *
 * 그래서 백엔드가 저장할 수 있는 코드를 여기서 모두 다룬다. 이 목록이 백엔드보다
 * 뒤처지면 `operations-center.test.ts`의 가드가 실패한다.
 */
export const SAFE_CAUSE_CODE_MESSAGES: Record<string, string> = {
  // 초기 진단(V0) 측정
  V0_REPORT_RETRIES_EXHAUSTED: '외부 AI 측정 재시도를 모두 사용했지만 초기 진단을 완료하지 못했습니다.',
  V0_PROVIDER_AUTH_OR_MODEL: 'AI 측정 공급자의 인증 또는 모델 설정을 확인해야 합니다.',
  V0_PROVIDER_UNAVAILABLE: '외부 AI 측정 서비스가 응답하지 않거나 일시적으로 제한되었습니다.',
  V0_JUDGE_FAILED: 'AI 답변은 받았지만 공통 언급 판정 단계에서 처리하지 못했습니다.',
  SOV_HIGH_PRIORITY_CAP_EXCEEDED: '이번 측정에 배정된 질문 수가 한도를 넘어 일부 질문을 측정하지 않았습니다.',
  // 콘텐츠 생성·발행
  PROVIDER_TIMEOUT: '콘텐츠 생성 서비스의 응답이 제시간에 오지 않았습니다.',
  PROVIDER_UNAVAILABLE: '콘텐츠 생성 서비스를 일시적으로 사용할 수 없습니다.',
  GENERATION_REJECTED: '콘텐츠 생성 서비스가 이번 요청을 처리하지 못했습니다.',
  MISSING_APPROVED_ESSENCE: '승인된 콘텐츠 운영 기준이 없어 자동 생성을 시작하지 않았습니다.',
  IMAGE_GENERATION_FAILED: '본문은 준비됐지만 대표 이미지를 만들지 못했습니다.',
  IMAGE_PROVIDER_UNAVAILABLE: '대표 이미지 생성 연결이 일시적으로 중단되었습니다.',
  GENERATION_LEASE_ACTIVE: '같은 콘텐츠의 다른 생성 작업이 아직 진행 중입니다.',
  STALE_GENERATION_CLAIM: '완료되지 않은 이전 작업 기록 때문에 새 생성을 시작하지 못했습니다.',
  CONTENT_NOT_GENERATED: '발행 시각까지 콘텐츠 제목과 본문이 준비되지 않았습니다.',
  CONTENT_GENERATION_PARTIAL: '이번 달 콘텐츠 중 일부만 생성됐습니다.',
  CONTENT_IMAGE_NOT_READY: '대표 이미지가 준비되지 않아 공개를 중단했습니다.',
  CONTENT_DISPATCH_FAILED: '콘텐츠 작업을 처리 대기열에 넣지 못했습니다.',
  MISSING_REFERENCES: '의료 콘텐츠에 필요한 참고 자료가 준비되지 않았습니다.',
  FORBIDDEN_EXPRESSION: '의료광고 금지 표현이 발견되어 공개를 중단했습니다.',
  ESSENCE_NOT_ALIGNED: '콘텐츠가 승인된 운영 기준의 자동 검사를 통과하지 못했습니다.',
  MONTHLY_SLOT_GENERATION_FAILED: '이번 달 발행 슬롯의 콘텐츠 생성이 완료되지 않았습니다.',
  // 운영 기준
  ESSENCE_AUTO_REVIEW_FAILED: '콘텐츠 운영 기준 자동 검수를 완료하지 못했습니다.',
  ESSENCE_AUTO_REVIEW_ESCALATED: '콘텐츠 운영 기준 자동 검수가 초안을 보류해 사람 확인이 필요합니다.',
  // 비용 안전장치
  COST_BLOCKED: '비용 안전장치가 이 작업의 실행을 보류했습니다.',
  COST_GUARD_LIMIT_REACHED: '오늘 설정된 사용 한도에 도달해 자동 작업을 보류했습니다.',
  // 리포트·공개 표면·도메인
  MONTHLY_REPORT_FAILED: '월간 리포트를 만드는 중 작업이 완료되지 않았습니다.',
  SITE_BUILD_DISPATCH_FAILED: '공개 정보 갱신 작업을 처리 대기열에 넣지 못했습니다.',
  CACHE_REVALIDATION_FAILED: '공개 표면의 내용 갱신을 확인하지 못했습니다.',
  DOMAIN_UNHEALTHY: '공개 주소가 정상으로 응답하지 않습니다.',
  // 알림·전달
  PUBLISH_NOTIFICATION_FAILED: '발행은 됐지만 담당자 알림을 보내지 못했습니다.',
  WEBHOOK_UNAVAILABLE: '알림 전송 대상이 응답하지 않습니다.',
  DELIVERY_OUTCOME_UNKNOWN: '알림을 보냈지만 전달 결과를 확인하지 못했습니다.',
  BROKER_UNAVAILABLE: '작업 처리 대기열에 연결하지 못해 작업을 시작하지 못했습니다.',
  // 상담 요청 무료 진단
  LEAD_DIAGNOSIS_FAILED: '상담 요청의 무료 진단 측정을 완료하지 못했습니다.',
  LEAD_DIAGNOSIS_RETRIES_EXHAUSTED: '무료 진단 재시도를 모두 사용했지만 측정을 완료하지 못했습니다.',
  LEAD_REPORT_RETRIES_EXHAUSTED: '무료 진단 리포트 재시도를 모두 사용했지만 리포트를 만들지 못했습니다.',
  LEAD_DELIVERY_ABANDONED: '무료 진단 리포트를 신청자에게 전달하지 못한 채 중단했습니다.',
  // 자료 수집
  NAVER_ITEMS_FAILED: '자료 수집에서 일부 글을 가져오지 못했습니다.',
  // 그 밖의 작업 실패
  TASK_FAILED: '자동 작업이 완료되지 않았습니다.',
  HOSPITAL_NOT_FOUND: '작업 대상 병원 정보를 찾지 못했습니다.',
  UNSAFE_STORED_DISPATCH: '저장된 작업 요청이 안전 검사를 통과하지 못해 다시 실행하지 않았습니다.',
  UNVERIFIED_ADMIN_ACTOR: '요청한 담당자 계정을 확인하지 못했습니다.',
}

export function safeCauseText(value: string | null | undefined): string {
  const cleaned = value?.trim() ?? ''
  if (SAFE_CAUSE_CODE_MESSAGES[cleaned]) return SAFE_CAUSE_CODE_MESSAGES[cleaned]
  const codeLike = /^[A-Z0-9_:-]+$/.test(cleaned)
  const sensitive = /[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|(?:https?|redis):\/\/|traceback|task[_ -]?id|api[_ -]?key|secret|token|exception|error|refused|timeout|\b0\d{1,2}[- ]?\d{3,4}[- ]?\d{4}\b/i.test(cleaned)
  const operatorReadable = /[가-힣]{2,}/.test(cleaned)
  if (!cleaned || cleaned.includes('원인 설명을 확인할 수 없습니다') || codeLike || sensitive || !operatorReadable) {
    return UNKNOWN_SAFE_CAUSE
  }
  return cleaned
}

export function effectiveSafeCause(detail: OperationsIncidentDetail): string {
  const candidates = [
    detail.incident.safe_cause,
    detail.run?.safe_error_message,
    detail.run?.safe_error_code,
    detail.incident.slack?.safe_error_message,
    detail.incident.slack?.safe_error_code,
  ]
  for (const candidate of candidates) {
    const safeCause = safeCauseText(candidate)
    if (safeCause !== UNKNOWN_SAFE_CAUSE) return safeCause
  }
  return UNKNOWN_SAFE_CAUSE
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
  const description = effectiveSafeCause(detail)
  return [
    '[운영 센터 개발팀 문의]',
    `병원: ${row.customer.name}`,
    `병원 ID: ${row.customer.hospital_id}`,
    `현상: ${operationStatusLabel(row.status)}`,
    `고객 영향: ${row.impact}`,
    `오류 식별자(개발팀용): ${code}`,
    `작업 실행 ID: ${detail.run?.run_id ?? row.operation_run_id ?? '기록되지 않음'}`,
    `재시도 횟수: ${detail.run?.attempt_count ?? '기록되지 않음'}`,
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

function incidentBase(row: OperationsQueueRow): string {
  const hospital = row.customer.hospital_id
  return hospital
    ? `/admin/operations/hospitals/${hospital}/incidents/${row.incident_id}`
    : `/admin/operations/incidents/${row.incident_id}`
}

function mutationFromPostAction(
  action: OperationsAction,
  row: OperationsQueueRow,
  reason: string,
): OperationsMutationDescriptor {
  return {
    kind: 'POST_ACTION',
    path: action.path,
    targetId: row.operation_run_id ?? row.incident_id ?? row.report_id ?? row.content_id ?? row.id,
    version: null,
    reason,
    label: action.label,
    requiresIdempotencyKey: Boolean(action.requires_idempotency_key),
  }
}

export function primaryOperationsMutation(
  detail: OperationsIncidentDetail,
  reason: string,
): OperationsMutationDescriptor | null {
  const row = detail.incident
  const run = detail.run
  const retry = enabledPostAction(run?.retry ?? null) ?? enabledPostAction(row.retry)
  if (retry?.kind === 'RETRY_RUN') {
    return {
      kind: 'RETRY_RUN',
      path: retry.path,
      targetId: run?.run_id ?? row.operation_run_id ?? row.id,
      version: null,
      reason,
      label: retry.label,
      requiresIdempotencyKey: true,
    }
  }
  const action = enabledPostAction(row.action)
  if (action?.kind === 'RECOVER_INCIDENT' || action?.kind === 'ACK_INCIDENT') {
    return {
      kind: action.kind,
      path: action.path,
      targetId: row.incident_id ?? row.id,
      version: row.version,
      reason,
      label: action.label,
      requiresIdempotencyKey: false,
    }
  }
  if (action) return mutationFromPostAction(action, row, reason)
  if (run?.state === 'SUCCEEDED' && row.status === 'RETRYING') {
    return {
      kind: 'RECOVER_INCIDENT',
      path: `${incidentBase(row)}/recover`,
      targetId: row.incident_id ?? row.id,
      version: row.version,
      reason,
      label: '복구 확인 완료',
      requiresIdempotencyKey: false,
    }
  }
  if (row.status === 'RECOVERED') {
    return {
      kind: 'ACK_INCIDENT',
      path: `${incidentBase(row)}/ack`,
      targetId: row.incident_id ?? row.id,
      version: row.version,
      reason,
      label: '문제 확인 완료',
      requiresIdempotencyKey: false,
    }
  }
  return null
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

/**
 * 처리 기한 한 줄.
 *
 * `sla_state`의 `DUE`는 "임박"이 아니라 "아직 지나지 않았다"는 뜻이다. 그런데 화면은
 * 그걸 전부 "처리 기한 임박"으로 적어서, 열흘 뒤 마감인 일과 두 시간 뒤 마감인 일이
 * 같은 문구를 달았다. 필터의 `DUE` 항목 라벨도 같은 오해를 담고 있었다.
 *
 * 남은 시간을 실제로 계산해서, 임박은 임박할 때만 말한다.
 */
export const DUE_SOON_HOURS = 24

export type OperationsDeadlineTone = 'overdue' | 'due_soon' | 'due' | 'none'

export interface OperationsDeadline {
  tone: OperationsDeadlineTone
  text: string
}

function formatDeadlineGap(hours: number): string {
  if (hours < 1) return `${Math.max(1, Math.floor(hours * 60))}분`
  if (hours < 48) return `${Math.floor(hours)}시간`
  return `${Math.floor(hours / 24)}일`
}

export function describeOperationsDeadline(
  row: { sla_state: string; sla_due_at: string | null },
  now: number,
  formatDate: (value: string) => string,
): OperationsDeadline {
  if (row.sla_state === 'NONE' || !row.sla_due_at) {
    return { tone: 'none', text: '처리 기한 없음' }
  }
  const dueAt = Date.parse(row.sla_due_at)
  if (!Number.isFinite(dueAt)) {
    return { tone: 'none', text: '처리 기한 확인 필요' }
  }

  const when = formatDate(row.sla_due_at)
  const gapHours = (dueAt - now) / 3_600_000
  if (row.sla_state === 'OVERDUE' || gapHours <= 0) {
    return { tone: 'overdue', text: `처리 기한 ${formatDeadlineGap(-gapHours)} 지남 · ${when}` }
  }
  if (gapHours <= DUE_SOON_HOURS) {
    return { tone: 'due_soon', text: `처리 기한 ${formatDeadlineGap(gapHours)} 남음 · ${when}` }
  }
  return { tone: 'due', text: `처리 기한 ${when}` }
}

/**
 * 목록 한 줄의 제목.
 *
 * 모든 줄의 제목이 병원 이름이었다. 한 병원에 오늘 확인할 콘텐츠가 세 편이면 같은 제목
 * 세 줄이 나란히 서고, 서로 무엇이 다른지는 작은 글씨의 "지금 할 일" 문장을 읽어야만
 * 알 수 있었다. 병원 이름 다음에 그 줄이 무슨 일인지 붙인다.
 */
const QUEUE_WORK_LABELS: Record<string, string> = {
  ONBOARDING: '온보딩 진행',
  TODAY: '오늘의 운영',
  REPORTS: '월간 리포트',
  INCIDENTS: '문제·복구',
}

export function operationsRowTitle(row: OperationsQueueRow): string {
  const work = operationStatusLabel(row.status)
  const fallback = QUEUE_WORK_LABELS[row.queue] ?? '운영 작업'
  return `${row.customer.name} · ${work === '상태 확인 필요' ? fallback : work}`
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
