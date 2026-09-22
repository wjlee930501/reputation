import { isRecord } from './type-guards.ts'
import {
  diagnosisBadges,
  diagnosisHint,
  isSuperseded,
  type AxisBadge,
  type LeadDiagnosisSummary,
} from './lead-diagnosis-status.ts'

/**
 * 생성 화면 하단의 콜용 진단 이력.
 *
 * 만든 뒤 "상담 요청에서 보세요"로 넘기면, 리드가 수십 건인 목록에서 방금 만든 것을
 * 찾아야 한다 — 실제로 그렇게 만들고 운영에서 못 찾았다. 만든 화면이 만든 결과를
 * 보여준다.
 */
export type ManualDiagnosisRow = {
  readonly id: string
  readonly leadId: string
  readonly clinicName: string
  readonly specialty: string | null
  readonly regionKeyword: string | null
  readonly coreKeywords: readonly string[]
  readonly badges: readonly AxisBadge[]
  readonly hint: string
  readonly superseded: boolean
  readonly reportHref: string | null
  readonly createdAt: string | null
}

function text(value: unknown): string | null {
  return typeof value === 'string' && value.length > 0 ? value : null
}

function keywords(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []
}

/** 준비된 보고서만 인증된 Admin 경로로 연다. 고객용 토큰은 화면에 노출하지 않는다. */
function reportHref(leadId: string, id: string, ready: boolean): string | null {
  if (!ready) return null
  return `/api/admin/leads/${encodeURIComponent(leadId)}/diagnoses/${encodeURIComponent(id)}/report`
}

function parseRow(value: unknown): ManualDiagnosisRow | null {
  if (!isRecord(value)) return null
  const id = text(value.id)
  const leadId = text(value.lead_id)
  if (!id || !leadId) return null

  // 상태 축 판정은 상담 요청 화면과 같은 규칙을 쓴다 — 두 화면이 같은 진단을 두고
  // 다른 말을 하면 어느 쪽을 믿어야 하는지 아무도 모른다.
  const summary: LeadDiagnosisSummary = {
    id,
    execution_status: text(value.execution_status) ?? '',
    report_status: text(value.report_status) ?? '',
    delivery_status: text(value.delivery_status) ?? '',
    superseded_at: text(value.superseded_at),
    superseded_by_id: text(value.superseded_by_id),
  }

  return {
    id,
    leadId,
    clinicName: text(value.clinic_name) ?? '병원명 확인 필요',
    specialty: text(value.specialty),
    regionKeyword: text(value.region_keyword),
    coreKeywords: keywords(value.core_keywords),
    badges: diagnosisBadges(summary),
    hint: diagnosisHint(summary),
    superseded: isSuperseded(summary),
    reportHref: reportHref(leadId, id, value.report_ready === true),
    createdAt: text(value.created_at),
  }
}

export function parseManualDiagnosisHistory(payload: unknown): readonly ManualDiagnosisRow[] {
  if (!isRecord(payload) || !Array.isArray(payload.items)) return []
  return payload.items.map(parseRow).filter((row): row is ManualDiagnosisRow => row !== null)
}

/** 무엇으로 측정했는지 한 줄. 이 값이 곧 질의라 틀리면 보고서가 통째로 빗나간다. */
export function measuredWith(row: ManualDiagnosisRow): string {
  return [row.specialty, row.regionKeyword, row.coreKeywords.join(', ')]
    .filter((part): part is string => Boolean(part && part.length > 0))
    .join(' · ')
}

/** 아직 결과를 기다리는 중인가. 목록이 스스로 새로고침할지 판단하는 데 쓴다. */
export function isInProgress(row: ManualDiagnosisRow): boolean {
  if (row.superseded) return false
  return row.badges.some((badge) => badge.tone === 'progress' || badge.label === '대기')
}
