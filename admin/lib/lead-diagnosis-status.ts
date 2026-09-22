/**
 * 무료 진단(1단) 상태 표시 규칙.
 *
 * 백엔드는 상태를 **3축으로 분리해서** 준다 — 측정(execution) / 보고서(report) / 고객 발송(delivery).
 * 단일 상태로 접으면 "측정은 일부 실패했지만 보고서는 고객에게 발송됐다" 같은 상태가 사라지는데,
 * 그 구분이 AE가 원장에게 무엇을 말할지 결정하는 정보다.
 *
 * 화면이 판정을 직접 하면 세 곳에서 조금씩 다르게 판정하게 되므로 여기 모아 둔다.
 */

export type ExecutionStatus = 'PENDING' | 'RUNNING' | 'SUCCEEDED' | 'PARTIAL' | 'FAILED'
export type ReportStatus = 'PENDING' | 'BUILDING' | 'READY' | 'BLOCKED' | 'PURGED'
export type DeliveryStatus = 'PENDING' | 'SENDING' | 'SENT' | 'FAILED' | 'INTERNAL'

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
  /** 갈음된 시각. 값이 있으면 이 진단은 더 이상 이 리드의 현재 판이 아니다. */
  superseded_at?: string | null
  superseded_by_id?: string | null
  recovery_runs?: DiagnosisRecoveryRuns
}

/**
 * 갈음된 진단인가.
 *
 * 입력이 틀린 채로 측정이 끝난 진단을 AE가 고쳐 다시 만들면 옛 행이 이렇게 남는다.
 * 지우지 않는 이유는 실제로 지출한 공급자 호출이 있기 때문이고, 그래서 화면에는
 * 보이되 **조치 대상이 아니라는 것**이 분명해야 한다.
 */
export function isSuperseded(diagnosis: LeadDiagnosisSummary): boolean {
  return Boolean(diagnosis.superseded_at)
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
  axis: '측정' | '보고서' | '고객 발송'
  label: string
  tone: Tone
}

const EXECUTION: Record<string, { label: string; tone: Tone }> = {
  PENDING: { label: '대기', tone: 'muted' },
  RUNNING: { label: '측정 중', tone: 'progress' },
  SUCCEEDED: { label: '완료', tone: 'ok' },
  // PARTIAL은 보고서를 낼 수 있지만 계획 미달이다 — 초록으로 칠하면 AE가 그 사실을 모른다.
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
  INTERNAL: { label: '내부 보관', tone: 'muted' },
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
    badge('보고서', REPORT, diagnosis.report_status),
    badge('고객 발송', DELIVERY, diagnosis.delivery_status),
  ]
}

/** AE가 손을 써야 하는가. 백엔드 판정을 신뢰하되, 없으면 같은 규칙으로 계산한다. */
export function needsAttention(diagnosis: LeadDiagnosisSummary): boolean {
  // 갈음된 진단은 사람이 이미 대체본을 만들었다 — 운영자 큐에 올리면 없는 일을 시킨다.
  if (isSuperseded(diagnosis)) return false
  if (typeof diagnosis.needs_attention === 'boolean') return diagnosis.needs_attention
  return (
    diagnosis.execution_status === 'FAILED' ||
    diagnosis.report_status === 'BLOCKED' ||
    diagnosis.delivery_status === 'FAILED'
  )
}

/** 재발송 버튼을 보여줄지. 보고서가 있어야 보낼 것이 있다. */
export function canRetryDelivery(diagnosis: LeadDiagnosisSummary): boolean {
  return (
    diagnosis.report_status === 'READY' &&
    (diagnosis.delivery_status === 'FAILED' || diagnosis.delivery_status === 'PENDING')
  )
}

/** 준비된 보고서만 인증된 Admin PDF 경로로 연다. 고객용 토큰은 화면에 노출하지 않는다. */
export function diagnosisReportHref(
  leadId: string,
  diagnosis: LeadDiagnosisSummary,
): string | null {
  // 갈음된 판의 보고서도 열 수 있어야 한다 — 무엇을 잘못 쟀는지 확인할 유일한 방법이다.
  if (diagnosis.report_status !== 'READY') return null
  return `/api/admin/leads/${encodeURIComponent(leadId)}/diagnoses/${encodeURIComponent(diagnosis.id)}/report`
}

/** 잠금 해제 버튼을 보여줄지. 이미 풀린 잠금을 다시 풀 수는 없다. */
export function canReleaseLock(diagnosis: LeadDiagnosisSummary): boolean {
  return diagnosis.delivery_status !== 'INTERNAL' && !diagnosis.lock_released_at
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
  // 갈음된 판은 복구하지 않는다. 고칠 것은 현재 판이다.
  if (isSuperseded(diagnosis)) return null
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
        description:
          diagnosis.delivery_status === 'INTERNAL'
            ? '이미 만들어진 콜용 보고서가 있어 자동 재측정으로 바꿀 수 없습니다.'
            : '이미 준비되었거나 고객에게 발송된 보고서가 있어 자동 재측정으로 바꿀 수 없습니다.',
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

  // 생성 실패(BLOCKED)뿐 아니라 이미 만들어진 보고서(READY)도 다시 만들 수 있다 —
  // 보고서 생성 로직을 고친 뒤 기존 진단에 적용할 길이 있어야 한다.
  if (diagnosis.report_status !== 'BLOCKED' && diagnosis.report_status !== 'READY') return null
  // 고객에게 나간 보고서는 제자리에서 갈아끼우지 않는다. 공개 링크가 최신 버전을 서빙하므로
  // 새 버전을 얹으면 신청자가 이미 받은 링크의 내용이 조용히 바뀐다. 백엔드도 같은 선을
  // 긋는다(lead_recovery._ensure_recoverable, ck_lead_diagnoses_delivery_requires_report).
  if (diagnosis.delivery_status !== 'PENDING' && diagnosis.delivery_status !== 'INTERNAL') {
    // 정상 발송된 보고서에까지 '개발팀 확인 필요'를 띄우면 조치할 일이 없는데 있는 것처럼 읽힌다.
    if (diagnosis.report_status !== 'BLOCKED') return null
    return {
      kind: 'support',
      enabled: false,
      label: '개발팀 확인 필요',
      description: '이미 고객에게 발송된 보고서 이력이 있어 자동으로 새 파일을 연결할 수 없습니다.',
      run: reportRun,
    }
  }
  if (
    diagnosis.execution_status !== 'SUCCEEDED' &&
    diagnosis.execution_status !== 'PARTIAL'
  ) {
    // 측정 결과가 없으면 만들 것도 없다. 다만 이미 보고서가 있는 진단에는 해당하지 않는다.
    if (diagnosis.report_status !== 'BLOCKED') return null
    return {
      kind: 'support',
      enabled: false,
      label: '측정 결과 확인 필요',
      description: '보고서를 만들 수 있는 측정 결과가 없어 운영 센터에서 확인해야 합니다.',
      run: reportRun,
    }
  }
  return {
    kind: 'rebuild',
    enabled: true,
    label: '보고서 다시 만들기',
    description:
      diagnosis.report_status === 'READY'
        ? '지금 보고서는 그대로 보관하고 최신 기준으로 새 보고서를 만듭니다.'
        : '기존 보고서는 보관하고 새 보고서를 만듭니다.',
    previousRun: reportRun?.state === 'FAILED' ? reportRun : null,
  }
}

/**
 * 이 진단에서 지금 가장 중요한 한 줄.
 *
 * 목록에서 배지 세 개를 다 읽게 만들면 아무도 안 읽는다 — 무엇을 해야 하는지를 문장으로 준다.
 */
export function diagnosisHint(diagnosis: LeadDiagnosisSummary): string {
  if (isSuperseded(diagnosis)) {
    return '값을 고쳐 새로 만든 진단으로 갈음됐습니다. 기록으로만 남깁니다.'
  }
  if (diagnosis.report_status === 'PURGED') return '개인정보가 파기된 진단입니다.'
  // 콜용(내부 보관) 진단에는 고객 발송 단계가 없다. 목록의 「콜용 / 고객 미발송」과 같은 말로,
  // 신청자에게 무엇이 갔는지가 아니라 AE가 콜에 쓸 보고서가 있는지만 말한다.
  if (diagnosis.delivery_status === 'INTERNAL') {
    if (diagnosis.report_status === 'READY') return '콜용 보고서가 준비됐습니다.'
    if (diagnosis.execution_status === 'FAILED' || diagnosis.report_status === 'BLOCKED') {
      return '콜용 보고서를 만들지 못했습니다.'
    }
    return '콜용 보고서를 만들고 있습니다.'
  }
  if (diagnosis.execution_status === 'FAILED') {
    return '측정이 다시 실패해 보고서를 만들지 못했습니다.'
  }
  if (diagnosis.report_status === 'BLOCKED') {
    return '보고서 생성이 재시도까지 실패했습니다. 신청자에게 이메일이 발송되지 않았습니다.'
  }
  if (diagnosis.delivery_status === 'FAILED') {
    return '보고서는 준비됐지만 이메일 발송이 실패했습니다. 재발송이 필요합니다.'
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

/** 이 상담 요청 전체를 대표하는 상태 — 목록 정렬·필터용. */
export function leadNeedsAttention(diagnoses: LeadDiagnosisSummary[] | undefined): boolean {
  return (diagnoses ?? []).some(needsAttention)
}
