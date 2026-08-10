export type ReportRunView = {
  readonly runId: string
  readonly parentRunId: string | null
  readonly periodYear: number
  readonly periodMonth: number
  readonly reportId: string | null
  readonly reportVersion: number | null
  readonly statusLabel: string
  readonly whatHappened: string
  readonly customerImpact: string
  readonly nextAction: string
  readonly versionLabel: string | null
  readonly canRebuild: boolean
  readonly primaryAction: 'wait' | 'review' | 'operations' | 'rebuild'
  readonly attentionLabel: string
  readonly isActive: boolean
  readonly requestedAt: string
  readonly completedAt: string | null
}

type RunCopy = Pick<
  ReportRunView,
  | 'statusLabel'
  | 'whatHappened'
  | 'customerImpact'
  | 'nextAction'
  | 'canRebuild'
  | 'primaryAction'
  | 'attentionLabel'
>

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function stringValue(value: unknown): string | null {
  return typeof value === 'string' && value.length > 0 ? value : null
}

function numberValue(value: unknown): number | null {
  return typeof value === 'number' && Number.isInteger(value) ? value : null
}

function copyForStage(stage: string): RunCopy {
  switch (stage) {
    case 'QUEUED':
      return {
        statusLabel: '리포트 생성 대기 중',
        whatHappened: '리포트 생성 요청이 순서대로 대기하고 있습니다.',
        customerImpact: '완료 전까지 원장님께 전달할 새 파일이 없습니다.',
        nextAction: '잠시 기다린 뒤 이 화면에서 진행 상태를 다시 확인해 주세요.',
        canRebuild: false,
        primaryAction: 'wait',
        attentionLabel: '진행 중',
      }
    case 'RUNNING':
      return {
        statusLabel: '리포트를 만들고 있습니다',
        whatHappened: '측정 결과를 모아 월간 리포트를 만드는 중입니다.',
        customerImpact: '아직 원장님께 전달할\u00a0수\u00a0없습니다.',
        nextAction: '완료될 때까지 기다린 뒤 새 리포트를 검수해 주세요.',
        canRebuild: false,
        primaryAction: 'wait',
        attentionLabel: '진행 중',
      }
    case 'BLOCKED':
      return {
        statusLabel: '필수 측정이 부족해 전달이 멈췄습니다',
        whatHappened: '리포트는 만들어졌지만 필수 측정이나 운영 자료가 부족합니다.',
        customerImpact: '현재 파일은 원장님께 전달할\u00a0수\u00a0없습니다.',
        nextAction: '운영 센터에서 차단 사유를 확인하고 해결한 뒤 ‘리포트 다시 만들기’를 눌러 주세요.',
        canRebuild: true,
        primaryAction: 'operations',
        attentionLabel: '조치 필요',
      }
    case 'COVERAGE_COMPLETE':
      return {
        statusLabel: '측정 집계가 완료됐습니다',
        whatHappened: '이번 달에 계획한 측정 결과가 모두 모였습니다.',
        customerImpact: '원장 전달용 PDF 확인이 끝나기 전에는 전달할\u00a0수\u00a0없습니다.',
        nextAction: '원장 전달용 PDF 준비 상태를 이어서 확인해 주세요.',
        canRebuild: false,
        primaryAction: 'review',
        attentionLabel: '검수 필요',
      }
    case 'ARTIFACT_VALIDATION_PENDING':
      return {
        statusLabel: '원장 전달용 PDF 확인이 필요합니다',
        whatHappened: '측정 집계와 리포트 생성은 끝났지만 원장 전달용 PDF 확인이 남았습니다.',
        customerImpact: '확인 전 파일은 원장님께 전달할\u00a0수\u00a0없습니다.',
        nextAction: '원장 전달용 PDF를 열어 글자·페이지·내용을 확인해 주세요.',
        canRebuild: false,
        primaryAction: 'review',
        attentionLabel: '검수 필요',
      }
    case 'ARTIFACT_VALIDATED':
      return {
        statusLabel: '원장 전달용 PDF 검증 완료',
        whatHappened: '원장 전달용 PDF의 한 페이지 구성, 한글, 필수 안내와 링크를 확인했습니다.',
        customerImpact: '최종 전달 가능 여부는 최신 병원 자료와 공개 상태를 함께 확인해야 합니다.',
        nextAction: '리포트 화면에서 최신 자료와 전달 가능 상태를 확인해 주세요.',
        canRebuild: false,
        primaryAction: 'review',
        attentionLabel: '최종 확인 필요',
      }
    case 'EXISTING':
      return {
        statusLabel: '기존 리포트가 있습니다',
        whatHappened: '같은 기간의 리포트가 있어 중복 생성을 건너뛰었습니다.',
        customerImpact: '기존 리포트는 그대로 보존됩니다.',
        nextAction: '기존 리포트를 검수하거나 변경 사항이 있으면 ‘리포트 다시 만들기’를 눌러 주세요.',
        canRebuild: true,
        primaryAction: 'review',
        attentionLabel: '확인 가능',
      }
    case 'FAILED':
    default:
      return {
        statusLabel: '리포트를 만들지 못했습니다',
        whatHappened: '월간 리포트를 끝까지\u00a0만들지\u00a0못했습니다.',
        customerImpact: '해당 월의 새 리포트를 원장님께 전달할\u00a0수\u00a0없습니다.',
        nextAction: '‘리포트 다시 만들기’를 눌러 주세요. 다시 실패하면 ‘개발팀 문의용 정보 복사’로 전달해 주세요.',
        canRebuild: true,
        primaryAction: 'rebuild',
        attentionLabel: '조치 필요',
      }
  }
}

function parseReportRun(value: unknown): ReportRunView | null {
  if (!isRecord(value)) return null
  const runId = stringValue(value.run_id)
  const stage = stringValue(value.stage)
  const periodYear = numberValue(value.period_year)
  const periodMonth = numberValue(value.period_month)
  const requestedAt = stringValue(value.requested_at)
  if (!runId || !stage || periodYear === null || periodMonth === null || !requestedAt) return null
  const reportVersion = numberValue(value.report_version)
  const supersedes = stringValue(value.supersedes_report_id)
  const copy = copyForStage(stage)
  return {
    runId,
    parentRunId: stringValue(value.parent_run_id),
    periodYear,
    periodMonth,
    reportId: stringValue(value.report_id),
    reportVersion,
    ...copy,
    isActive: stage === 'QUEUED' || stage === 'RUNNING',
    versionLabel: reportVersion === null
      ? null
      : supersedes
        ? `새 버전 ${reportVersion} · 이전 리포트 보존`
        : `버전 ${reportVersion}`,
    requestedAt,
    completedAt: stringValue(value.completed_at),
  }
}

export function parseReportRuns(value: unknown): readonly ReportRunView[] {
  if (!Array.isArray(value)) return []
  return value.map(parseReportRun).filter((run): run is ReportRunView => run !== null)
}

export function reportRunDeveloperNote(hospitalId: string, run: ReportRunView): string {
  return [
    '월간 리포트 작업 확인 요청',
    `병원 ID: ${hospitalId}`,
    `작업 ID: ${run.runId}`,
    `대상 월: ${run.periodYear}-${String(run.periodMonth).padStart(2, '0')}`,
    `확인 시각: ${new Date().toISOString()}`,
  ].join('\n')
}

export function isValidReportRebuildReason(value: string): boolean {
  return value.trim().length >= 3 && value.trim().length <= 200
}

export function reportRebuildIdempotencyKey(runId: string, requestId: string): string {
  return `monthly-report-rebuild:${runId}:${requestId}`
}

export function reportRebuildFingerprint(
  runId: string,
  periodYear: number,
  periodMonth: number,
  reason: string,
): string {
  return [runId, periodYear, periodMonth, reason.trim()].join('\u0000')
}

export function getOrCreateReportRequestKey(
  cache: Map<string, string>,
  fingerprint: string,
  create: () => string,
): string {
  const existing = cache.get(fingerprint)
  if (existing) return existing
  const created = create()
  cache.set(fingerprint, created)
  return created
}
