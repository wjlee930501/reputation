/**
 * 무료 진단(1단) 상태 표시 규칙.
 *
 * 백엔드는 상태를 **3축으로 분리해서** 준다 — 측정(execution) / 리포트(report) / 발송(delivery).
 * 단일 상태로 접으면 "측정은 일부 실패했지만 리포트는 나갔다" 같은 상태가 사라지는데,
 * 그 구분이 AE가 원장에게 무엇을 말할지 결정하는 정보다.
 *
 * 화면이 판정을 직접 하면 세 곳에서 조금씩 다르게 판정하게 되므로 여기 모아 둔다.
 */

export type ExecutionStatus = 'PENDING' | 'RUNNING' | 'SUCCEEDED' | 'PARTIAL' | 'FAILED'
export type ReportStatus = 'PENDING' | 'BUILDING' | 'READY' | 'BLOCKED' | 'PURGED'
export type DeliveryStatus = 'PENDING' | 'SENDING' | 'SENT' | 'FAILED'

export interface LeadDiagnosisSummary {
  id: string
  execution_status: ExecutionStatus | string
  execution_attempts?: number
  report_status: ReportStatus | string
  report_attempts?: number
  delivery_status: DeliveryStatus | string
  slot_date?: string | null
  slot_no?: number | null
  lock_released_at?: string | null
  lock_released_by?: string | null
  needs_attention?: boolean
  error?: string | null
  created_at?: string | null
  recovery_runs?: DiagnosisRecoveryRuns
}

export type RecoveryRunState =
  | 'REQUESTED'
  | 'QUEUED'
  | 'RUNNING'
  | 'SUCCEEDED'
  | 'PARTIAL'
  | 'FAILED'
  | 'CANCELLED'

export interface DiagnosisRecoveryRun {
  readonly id: string
  readonly state: RecoveryRunState | string
  readonly requested_at: string | null
  readonly safe_error_code?: string | null
}

export interface DiagnosisRecoveryRuns {
  readonly measurement: DiagnosisRecoveryRun | null
  readonly report: DiagnosisRecoveryRun | null
}

export type DiagnosisRecoveryAction =
  | {
      readonly kind: 'remeasure' | 'rebuild'
      readonly enabled: true
      readonly label: string
      readonly description: string
      readonly previousRun: DiagnosisRecoveryRun | null
    }
  | {
      readonly kind: 'progress' | 'support'
      readonly enabled: false
      readonly label: string
      readonly description: string
      readonly run: DiagnosisRecoveryRun | null
    }

export type Tone = 'ok' | 'progress' | 'warn' | 'danger' | 'muted'

export interface AxisBadge {
  axis: '측정' | '리포트' | '발송'
  label: string
  tone: Tone
}

const EXECUTION: Record<string, { label: string; tone: Tone }> = {
  PENDING: { label: '대기', tone: 'muted' },
  RUNNING: { label: '측정 중', tone: 'progress' },
  SUCCEEDED: { label: '완료', tone: 'ok' },
  // PARTIAL은 리포트를 낼 수 있지만 계획 미달이다 — 초록으로 칠하면 AE가 그 사실을 모른다.
  PARTIAL: { label: '일부 실패', tone: 'warn' },
  FAILED: { label: '실패', tone: 'danger' },
}

const REPORT: Record<string, { label: string; tone: Tone }> = {
  PENDING: { label: '대기', tone: 'muted' },
  BUILDING: { label: '생성 중', tone: 'progress' },
  READY: { label: '준비됨', tone: 'ok' },
  BLOCKED: { label: '생성 실패', tone: 'danger' },
  PURGED: { label: '파기됨', tone: 'muted' },
}

const DELIVERY: Record<string, { label: string; tone: Tone }> = {
  PENDING: { label: '대기', tone: 'muted' },
  SENDING: { label: '발송 중', tone: 'progress' },
  SENT: { label: '발송 완료', tone: 'ok' },
  FAILED: { label: '발송 실패', tone: 'danger' },
}

function badge(
  axis: AxisBadge['axis'],
  table: Record<string, { label: string; tone: Tone }>,
  value: string,
): AxisBadge {
  const hit = table[value]
  return { axis, label: hit?.label ?? '확인 필요', tone: hit?.tone ?? 'muted' }
}

export function diagnosisBadges(diagnosis: LeadDiagnosisSummary): AxisBadge[] {
  return [
    badge('측정', EXECUTION, diagnosis.execution_status),
    badge('리포트', REPORT, diagnosis.report_status),
    badge('발송', DELIVERY, diagnosis.delivery_status),
  ]
}

/** AE가 손을 써야 하는가. 백엔드 판정을 신뢰하되, 없으면 같은 규칙으로 계산한다. */
export function needsAttention(diagnosis: LeadDiagnosisSummary): boolean {
  if (typeof diagnosis.needs_attention === 'boolean') return diagnosis.needs_attention
  return (
    diagnosis.execution_status === 'FAILED' ||
    diagnosis.report_status === 'BLOCKED' ||
    diagnosis.delivery_status === 'FAILED'
  )
}

/** 재발송 버튼을 보여줄지. 리포트가 있어야 보낼 것이 있다. */
export function canRetryDelivery(diagnosis: LeadDiagnosisSummary): boolean {
  return (
    diagnosis.report_status === 'READY' &&
    (diagnosis.delivery_status === 'FAILED' || diagnosis.delivery_status === 'PENDING')
  )
}

/** 준비된 리포트만 인증된 Admin PDF 경로로 연다. 고객용 토큰은 화면에 노출하지 않는다. */
export function diagnosisReportHref(
  leadId: string,
  diagnosis: LeadDiagnosisSummary,
): string | null {
  if (diagnosis.report_status !== 'READY') return null
  return `/api/admin/leads/${encodeURIComponent(leadId)}/diagnoses/${encodeURIComponent(diagnosis.id)}/report`
}

/** 잠금 해제 버튼을 보여줄지. 이미 풀린 잠금을 다시 풀 수는 없다. */
export function canReleaseLock(diagnosis: LeadDiagnosisSummary): boolean {
  return !diagnosis.lock_released_at
}

const ACTIVE_RECOVERY_STATES = new Set(['REQUESTED', 'QUEUED', 'RUNNING'])

function activeRecoveryRun(diagnosis: LeadDiagnosisSummary): DiagnosisRecoveryRun | null {
  const runs = diagnosis.recovery_runs
  if (!runs) return null
  if (runs.measurement && ACTIVE_RECOVERY_STATES.has(runs.measurement.state)) {
    return runs.measurement
  }
  if (runs.report && ACTIVE_RECOVERY_STATES.has(runs.report.state)) return runs.report
  return null
}

/** 한 진단에는 항상 하나의 권위 있는 복구 행동만 노출한다. */
export function recoveryAction(
  diagnosis: LeadDiagnosisSummary,
): DiagnosisRecoveryAction | null {
  const active = activeRecoveryRun(diagnosis)
  if (active) {
    return {
      kind: 'progress',
      enabled: false,
      label: '복구 작업 진행 중',
      description: '요청이 접수되어 처리 중입니다. 완료되면 이 상태가 자동으로 바뀝니다.',
      run: active,
    }
  }

  const measurementRun = diagnosis.recovery_runs?.measurement ?? null
  const reportRun = diagnosis.recovery_runs?.report ?? null
  if (diagnosis.execution_status === 'FAILED') {
    if (
      diagnosis.report_status === 'READY' ||
      diagnosis.report_status === 'PURGED' ||
      diagnosis.delivery_status === 'SENT' ||
      diagnosis.delivery_status === 'SENDING'
    ) {
      return {
        kind: 'support',
        enabled: false,
        label: '개발팀 확인 필요',
        description: '이미 준비되었거나 전달된 리포트가 있어 자동 재측정으로 바꿀 수 없습니다.',
        run: measurementRun,
      }
    }
    return {
      kind: 'remeasure',
      enabled: true,
      label: '다시 측정',
      description: '같은 환자 질문을 AI에 다시 물어 병원명이 확인되는지 측정합니다.',
      previousRun: measurementRun?.state === 'FAILED' ? measurementRun : null,
    }
  }

  if (diagnosis.report_status !== 'BLOCKED') return null
  if (diagnosis.delivery_status === 'SENT' || diagnosis.delivery_status === 'SENDING') {
    return {
      kind: 'support',
      enabled: false,
      label: '개발팀 확인 필요',
      description: '이미 전달된 리포트 이력이 있어 자동으로 새 파일을 연결할 수 없습니다.',
      run: reportRun,
    }
  }
  if (
    diagnosis.execution_status !== 'SUCCEEDED' &&
    diagnosis.execution_status !== 'PARTIAL'
  ) {
    return {
      kind: 'support',
      enabled: false,
      label: '측정 결과 확인 필요',
      description: '리포트를 만들 수 있는 측정 결과가 없어 운영센터에서 확인해야 합니다.',
      run: reportRun,
    }
  }
  return {
    kind: 'rebuild',
    enabled: true,
    label: '리포트 다시 만들기',
    description: '기존 리포트는 보관하고 새 리포트를 만듭니다.',
    previousRun: reportRun?.state === 'FAILED' ? reportRun : null,
  }
}

/**
 * 이 진단에서 지금 가장 중요한 한 줄.
 *
 * 목록에서 배지 세 개를 다 읽게 만들면 아무도 안 읽는다 — 무엇을 해야 하는지를 문장으로 준다.
 */
export function diagnosisHint(diagnosis: LeadDiagnosisSummary): string {
  if (diagnosis.report_status === 'PURGED') return '개인정보가 파기된 진단입니다.'
  if (diagnosis.execution_status === 'FAILED') {
    return '측정이 다시 실패해 리포트를 만들지 못했습니다.'
  }
  if (diagnosis.report_status === 'BLOCKED') {
    return '리포트 생성이 재시도까지 실패했습니다. 신청자에게 메일이 나가지 않았습니다.'
  }
  if (diagnosis.delivery_status === 'FAILED') {
    return '리포트는 준비됐지만 메일 발송이 실패했습니다. 재발송이 필요합니다.'
  }
  if (diagnosis.delivery_status === 'SENT') {
    return diagnosis.execution_status === 'PARTIAL'
      ? '발송 완료 — 단, 측정 일부가 실패했습니다(표본이 계획보다 적음).'
      : '발송 완료.'
  }
  if (diagnosis.execution_status === 'RUNNING' || diagnosis.report_status === 'BUILDING') {
    return '진행 중으로 접수되었습니다. 완료 여부는 이 화면에서 확인해 주세요.'
  }
  return '대기 중입니다.'
}

/** 이 리드 전체를 대표하는 상태 — 목록 정렬·필터용. */
export function leadNeedsAttention(diagnoses: LeadDiagnosisSummary[] | undefined): boolean {
  return (diagnoses ?? []).some(needsAttention)
}
