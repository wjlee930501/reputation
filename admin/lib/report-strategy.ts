export interface ReportStrategyTarget {
  name: string
  sovPct: number | null
  platformSov: Record<string, number | null>
  sourceBackedCount: number
  successfulMeasurementCount: number
  competitorOutcomes: Array<{ name: string; mentionPct: number }>
}

export interface ReportStrategyItem {
  title: string
  queryTargetName: string | null
  description: string | null
  owner: string | null
  dueMonth: string | null
  linkedContentTitle: string | null
}

export interface ReportStrategyGap {
  queryTargetName: string | null
  gapType: string
  gapTypeLabel: string
  severity: string
  severityLabel: string
}

export interface ReportStrategy {
  queryTargets: ReportStrategyTarget[]
  exposureGaps: ReportStrategyGap[]
  completedActions: ReportStrategyItem[]
  nextMonth: string
  nextMonthActions: ReportStrategyItem[]
  complianceCaveat: string
}

function object(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null
}

function string(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value : null
}

function number(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function action(value: unknown): ReportStrategyItem | null {
  const row = object(value)
  const title = string(row?.title)
  if (!row || !title) return null
  return {
    title,
    queryTargetName: string(row.query_target_name),
    description: string(row.description),
    owner: string(row.owner),
    dueMonth: string(row.due_month),
    linkedContentTitle: string(row.linked_content_title),
  }
}

export function readReportStrategy(contentSummary: Record<string, unknown> | null): ReportStrategy | null {
  const raw = object(contentSummary?.strategy)
  if (!raw) return null
  const queryTargets = (Array.isArray(raw.query_targets) ? raw.query_targets : []).flatMap((value) => {
    const row = object(value)
    const name = string(row?.name)
    if (!row || !name) return []
    const platformSov: Record<string, number | null> = {}
    for (const [platform, value] of Object.entries(object(row.platform_sov) ?? {})) {
      platformSov[platform] = number(value)
    }
    const competitorOutcomes = (Array.isArray(row.competitor_outcomes) ? row.competitor_outcomes : []).flatMap((value) => {
      const competitor = object(value)
      const competitorName = string(competitor?.name)
      const mentionPct = number(competitor?.mention_pct)
      return competitor && competitorName && mentionPct !== null ? [{ name: competitorName, mentionPct }] : []
    })
    return [{
      name,
      sovPct: number(row.sov_pct),
      platformSov,
      sourceBackedCount: number(row.source_backed_count) ?? 0,
      successfulMeasurementCount: number(row.successful_measurement_count) ?? 0,
      competitorOutcomes,
    }]
  })
  const exposureGaps = (Array.isArray(raw.exposure_gaps) ? raw.exposure_gaps : []).flatMap((value) => {
    const row = object(value)
    const gapType = string(row?.gap_type)
    if (!row || !gapType) return []
    const severity = string(row.severity) ?? '-'
    return [{
      queryTargetName: string(row.query_target_name),
      gapType,
      gapTypeLabel: string(row.gap_type_label) ?? gapType,
      severity,
      severityLabel: string(row.severity_label) ?? severity,
    }]
  })
  return {
    queryTargets,
    exposureGaps,
    completedActions: (Array.isArray(raw.completed_actions) ? raw.completed_actions : []).flatMap((value) => action(value) ?? []),
    nextMonth: string(raw.next_month) ?? '다음 달',
    nextMonthActions: (Array.isArray(raw.next_month_actions) ? raw.next_month_actions : []).flatMap((value) => action(value) ?? []),
    complianceCaveat: string(raw.compliance_caveat) ?? '',
  }
}
