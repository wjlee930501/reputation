export type MeasurementCell = {
  queryKey: string
  queryText: string
  queryLabel: string
  platformLabel: string
  stateLabel: string
  measured: boolean
  mentioned: boolean
}
export type ActionCopy = {
  label: string
  problem: string
  customerImpact: string
  nextAction: string
}
export type ReportEvent = {
  id: string
  type: string
  recipient: string
  channel: string
  operator: string
  note: string | null
  reason: string | null
  createdAt: string
}
export type MentionEvidence = ActionCopy & {
  queryText: string
  platformLabel: string
  relatedContents: readonly string[]
}
export type CitationEvidence = {
  measuredCells: number
  citedCells: number
  citedContentCount: number
}
export type ContentOperationsEvidence = ActionCopy & {
  planQuota: number | null
  publishedCount: number
  shortfallCount: number
  scheduledSlotCount: number
  scheduledSlotStateCounts: Readonly<Record<string, number>>
  requiredReviewCount: number
  reviewedCount: number
  pendingReviewCount: number
  overdueReviewCount: number
  cutoffAt: string | null
  deliveryWarnings: readonly string[]
}
export type PlatformMeasurementEvidence = {
  platform: string
  platformLabel: string
  answerModels: readonly string[]
  modelObservationComplete: boolean
  searchObservedCount: number
  searchUsedCount: number
}

export type ReportView = {
  id: string
  hospitalId: string
  periodYear: number
  periodMonth: number
  typeLabel: string
  statusLabel: string
  hasPdf: boolean
  internalDownloadUrl: string | null
  createdAt: string
  sentAt: string | null
  /**
   * 검증본 sha256에 묶인 전달 기록 파이프라인의 대상인지(월간만 true).
   *
   * 초기 진단(V0)은 AE가 PDF를 직접 원장에게 전달하므로 전달 이벤트를 남기지 않는다.
   * 이 값을 보지 않으면 V0 행이 월간 전달 서사("전달 전 검수 가능")로 읽힌다.
   */
  deliveryTracked: boolean
  deliveryReady: boolean
  deliveryBlockers: readonly string[]
  doctorArtifact: {
    state: 'MISSING' | 'INVALID' | 'VALID'
    stateLabel: string
    sha256: string | null
    pageCount: number | null
    validatedAt: string | null
  }
  review: {
    version: number
    versionLabel: string
    supersedesReportId: string | null
    measurement: ActionCopy & {
      quality: string
      plannedCount: number
      successCount: number
      failedCount: number
      excludedCount: number
    }
    notification: ActionCopy & {
      state: string
      sentAt: string | null
      operationsUrl: string
    }
  } | null
  sovPct: number | null
  comparison: Omit<ActionCopy, 'label'> & {
    comparable: boolean
    currentPct: number | null
    priorPct: number | null
    changePct: number | null
  } | null
  cells: readonly MeasurementCell[]
  platforms: readonly PlatformMeasurementEvidence[]
  mentions: readonly MentionEvidence[]
  /**
   * AI 답변이 병원 공개 표면(허브·발행 글)을 인용한 관찰 집계.
   *
   * `citations` 키가 없던 과거 리포트는 null이다 — 화면은 이 경우 줄을 감춘다.
   */
  citations: CitationEvidence | null
  contentOperations: ContentOperationsEvidence | null
  deliveryHistory: readonly ReportEvent[]
  effectiveEventType: string | null
}

export const REPORT_REVIEW_SECTION_ORDER = [
  'status',
  'measurement',
  'operations',
  'artifact',
  'notification',
  'delivery',
] as const

function record(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null
}

function text(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback
}

function number(value: unknown, fallback = 0): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback
}

function nullableNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []
}

function actionCopy(value: unknown, label = ''): ActionCopy {
  const item = record(value)
  return {
    label: text(item?.quality_label ?? item?.state_label ?? item?.classification_label, label),
    problem: text(item?.problem ?? item?.meaning, '상태 설명을 확인할 수 없습니다.'),
    customerImpact: text(item?.customer_impact, '고객 영향을 확인할 수 없습니다.'),
    nextAction: text(item?.next_action, '새로고침 후 계속 보이지 않으면 개발팀에 문의해 주세요.'),
  }
}

function parseCells(value: unknown): MeasurementCell[] {
  if (!Array.isArray(value)) return []
  return value.flatMap((raw) => {
    const cell = record(raw)
    const queryKey = text(cell?.query_key)
    if (!cell || !queryKey) return []
    return [{
      queryKey,
      queryText: text(cell.query_text, '질문 내용 없음'),
      queryLabel: text(cell.query_intent_label, '질문'),
      platformLabel: text(cell.platform_label, 'AI 서비스'),
      stateLabel: text(cell.state_label, '상태 확인 필요'),
      measured: cell.measured === true,
      mentioned: cell.mentioned === true,
    }]
  })
}

function parsePlatforms(value: unknown): PlatformMeasurementEvidence[] {
  if (!Array.isArray(value)) return []
  return value.flatMap((raw) => {
    const item = record(raw)
    const platform = text(item?.platform)
    if (!item || !platform) return []
    return [{
      platform,
      platformLabel: text(item.platform_label, platform),
      answerModels: strings(item.answer_models),
      modelObservationComplete: item.model_observation_complete === true,
      searchObservedCount: number(item.search_observed_count),
      searchUsedCount: number(item.search_used_count),
    }]
  })
}

function parseEvents(value: unknown): ReportEvent[] {
  if (!Array.isArray(value)) return []
  return value.flatMap((raw, index) => {
    const event = record(raw)
    if (!event) return []
    return [{
      id: text(event.id, `event-${index}`),
      type: text(event.event_type),
      recipient: text(event.recipient_label, '-'),
      channel: text(event.channel, '-'),
      operator: text(event.operator, '-'),
      note: text(event.note) || null,
      reason: text(event.reason) || null,
      createdAt: text(event.created_at),
    }]
  })
}

function parseMentions(value: unknown): MentionEvidence[] {
  const content = record(value)
  const rows = [
    ...(Array.isArray(content?.new_mention_cells) ? content.new_mention_cells : []),
    ...(Array.isArray(content?.first_measured_mention_cells) ? content.first_measured_mention_cells : []),
    ...(Array.isArray(content?.non_comparable_cells) ? content.non_comparable_cells : []),
  ]
  return rows.flatMap((raw) => {
    const item = record(raw)
    if (!item) return []
    return [{
      ...actionCopy(item),
      queryText: text(item.query_text, '질문 내용 없음'),
      platformLabel: text(item.platform_label, 'AI 서비스'),
      relatedContents: strings(item.related_contents),
    }]
  })
}

function parseCitations(value: unknown): CitationEvidence | null {
  const citations = record(record(value)?.citations)
  if (!citations) return null
  return {
    measuredCells: number(citations.measured_cell_count),
    citedCells: number(citations.cited_cell_count),
    citedContentCount: number(citations.cited_content_count),
  }
}

function parseContentOperations(value: unknown): ContentOperationsEvidence | null {
  const content = record(value)
  const operations = record(content?.operations)
  if (!operations) return null
  const review = record(operations.post_publish_review)
  const copy = actionCopy(operations.operator_copy, '콘텐츠 운영 증거')
  const rawStateCounts = record(operations.scheduled_slot_state_counts)
  const scheduledSlotStateCounts: Record<string, number> = {}
  for (const [key, raw] of Object.entries(rawStateCounts ?? {})) {
    const value = number(raw)
    if (value > 0) scheduledSlotStateCounts[key] = value
  }
  return {
    ...copy,
    planQuota: nullableNumber(operations.plan_quota),
    publishedCount: number(operations.published_count),
    shortfallCount: number(operations.shortfall_count),
    scheduledSlotCount: number(operations.scheduled_slot_count),
    scheduledSlotStateCounts,
    requiredReviewCount: number(review?.required_sample_count),
    reviewedCount: number(review?.reviewed_count),
    pendingReviewCount: number(review?.pending_count),
    overdueReviewCount: number(review?.overdue_count),
    cutoffAt: text(review?.cutoff_at) || null,
    deliveryWarnings: strings(operations.delivery_warnings),
  }
}

export function parseReport(value: unknown): ReportView | null {
  const root = record(value)
  const id = text(root?.id)
  const hospitalId = text(root?.hospital_id)
  if (!root || !id || !hospitalId) return null
  const display = record(root.display)
  const artifact = record(root.doctor_artifact)
  const evidence = record(root.review_evidence)
  const measurement = record(evidence?.measurement)
  const notification = record(evidence?.notification)
  const sov = record(root.sov_summary)
  const comparison = record(sov?.comparison)
  const effective = record(root.effective_delivery)
  const artifactState = text(artifact?.state ?? root.doctor_artifact_state, 'MISSING')
  return {
    id,
    hospitalId,
    periodYear: number(root.period_year),
    periodMonth: number(root.period_month),
    typeLabel: text(display?.report_type_label, text(root.report_type, '리포트')),
    statusLabel: text(display?.screening_status_label, '검수 필요'),
    hasPdf: root.has_pdf === true,
    internalDownloadUrl: text(root.download_url) || null,
    createdAt: text(root.created_at),
    sentAt: text(root.sent_at) || null,
    // 값을 못 읽으면 월간(전달 추적 대상)으로 본다 — 전달 서사를 잘못 감추는 쪽보다
    // 잘못 보여주는 쪽이 안전하다(전달 실행은 sha256 검사로 따로 막힌다).
    deliveryTracked: root.delivery_tracked !== false,
    deliveryReady: root.delivery_ready === true,
    deliveryBlockers: strings(root.delivery_blockers),
    doctorArtifact: {
      state: artifactState === 'VALID' || artifactState === 'INVALID' ? artifactState : 'MISSING',
      stateLabel: text(artifact?.state_label, '원장 전달용 PDF를 확인할 수 없습니다'),
      sha256: text(artifact?.sha256 ?? root.doctor_artifact_sha256) || null,
      pageCount: nullableNumber(artifact?.page_count),
      validatedAt: text(artifact?.validated_at) || null,
    },
    review: evidence && measurement && notification ? {
      version: number(evidence.version, 1),
      versionLabel: text(evidence.version_label, '버전 확인 필요'),
      supersedesReportId: text(evidence.supersedes_report_id) || null,
      measurement: {
        ...actionCopy(measurement),
        quality: text(measurement.quality),
        plannedCount: number(measurement.planned_count),
        successCount: number(measurement.success_count),
        failedCount: number(measurement.failed_count),
        excludedCount: number(measurement.excluded_count),
      },
      notification: {
        ...actionCopy(notification),
        state: text(notification.state),
        sentAt: text(notification.sent_at) || null,
        operationsUrl: text(notification.operations_url, '/operations?queue=REPORTS'),
      },
    } : null,
    sovPct: nullableNumber(sov?.sov_pct),
    comparison: comparison ? {
      ...actionCopy(comparison),
      comparable: comparison.status === 'COMPARABLE',
      currentPct: nullableNumber(comparison.current_sov_pct),
      priorPct: nullableNumber(comparison.prior_sov_pct),
      changePct: nullableNumber(comparison.change_pct),
    } : null,
    cells: parseCells(sov?.cells),
    platforms: parsePlatforms(sov?.platforms),
    mentions: parseMentions(root.content_summary),
    citations: parseCitations(root.content_summary),
    contentOperations: parseContentOperations(root.content_summary),
    deliveryHistory: parseEvents(root.delivery_history),
    effectiveEventType: text(effective?.event_type) || null,
  }
}

export function parseReports(value: unknown): ReportView[] {
  return Array.isArray(value)
    ? value.map(parseReport).filter((report): report is ReportView => report !== null)
    : []
}
