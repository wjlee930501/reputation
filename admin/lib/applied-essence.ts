import { ADMIN_COPY } from './admin-copy.ts'
import type { ContentPhilosophy } from '../types/index.ts'

export interface AppliedEssenceRow {
  label: string
  value: string
}

function appliedAtText(value: string | null | undefined): string {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '-'
  return date.toLocaleString('ko-KR', { timeZone: 'Asia/Seoul', hour12: false })
}

/** 현재 적용 중인 운영 기준을 읽기 전용 패널의 줄로 바꾼다(Phase 4 D33). */
export function appliedEssenceSummary(philosophy: ContentPhilosophy): AppliedEssenceRow[] {
  const metadata = philosophy.synthesis_metadata ?? {}
  const automatic = metadata.mode === 'AUTOMATIC'
  const mode = !automatic
    ? ADMIN_COPY.appliedEssenceModeLegacy
    : metadata.fallback
      ? ADMIN_COPY.appliedEssenceModeFallback
      : ADMIN_COPY.appliedEssenceModeAutomatic
  const inputCount = automatic
    ? Object.keys(metadata.input_hashes ?? {}).length
    : philosophy.source_asset_ids.length
  const rows: AppliedEssenceRow[] = [
    { label: ADMIN_COPY.appliedEssenceVersion, value: `v${philosophy.version}` },
    { label: ADMIN_COPY.appliedEssenceAppliedAt, value: appliedAtText(philosophy.approved_at) },
    { label: ADMIN_COPY.appliedEssenceMode, value: mode },
    { label: ADMIN_COPY.appliedEssenceSources, value: `${inputCount}건` },
  ]
  if (automatic) {
    rows.push(
      {
        label: ADMIN_COPY.appliedEssenceExcluded,
        value: `${(metadata.excluded_inputs ?? []).length}건`,
      },
      { label: ADMIN_COPY.appliedEssenceDropped, value: `${metadata.dropped_total ?? 0}건` },
      {
        label: ADMIN_COPY.appliedEssenceConflicts,
        value: `${(metadata.conflicts ?? []).length}건`,
      },
    )
  }
  return rows
}
