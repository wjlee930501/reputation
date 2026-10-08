/**
 * 발행 일정을 저장하기 전에 남은 일과, 그 일이 자동으로 진행 중이라는 사실.
 *
 * 자료 처리·운영 기준 합성은 사람이 시작하는 일이 아니다. 자료를 올려 두면 서버가
 * 이어서 처리한다. 그래서 여기 문구는 "무엇을 누르라"가 아니라 "지금 무엇이 되고
 * 있는지"만 말한다 — admin에는 처리를 시작하는 버튼이 없으므로, 서버가 보낸
 * `next_action`을 그대로 쓰면 없는 버튼을 누르라는 지시가 화면에 나온다.
 */

import { ADMIN_COPY } from './admin-copy.ts'

export interface ReadinessCheck {
  key: string
  label: string
  passed: boolean
  next_action?: string | null
}

export interface ContentReadiness {
  schedule_availability?: {
    available: boolean
    blockers: Array<{ code: string; message: string }>
  } | null
  essence?: {
    processed_source_count?: number | null
    required_source_count?: number | null
    approved_philosophy_exists?: boolean | null
    source_stale?: boolean | null
    /** 지금 자동으로 처리 중인 자료 수 (essence 안에 내려온다). 서버가 아직 안 내려주면 없다. */
    processing_source_count?: number | null
  } | null
  checks?: ReadinessCheck[]
}

export function contentReadinessBlockers(readiness: ContentReadiness | null): string[] {
  if (!readiness) return []
  const availability = readiness.schedule_availability
  if (!availability) return ['발행 일정 가능 여부를 서버에서 확인할 수 없습니다.']
  return Array.from(new Set(availability.blockers.map((blocker) => blocker.message)))
}

/**
 * 지금 자동으로 진행 중인 자료 처리. 서버가 세어 준 값이 없으면 아무 말도 하지 않는다 —
 * 모르는 건수를 0건이라고 말하면 "아무 일도 안 하고 있다"로 읽힌다. 서버가 내려주는
 * 건수는 `processing_source_count` 하나뿐이므로 대기 건수를 따로 말하지 않는다.
 */
export function sourceProcessingProgress(readiness: ContentReadiness | null): string | null {
  const processing = readiness?.essence?.processing_source_count
  if (typeof processing !== 'number') return null
  return `${ADMIN_COPY.evidence} 처리 중 ${processing}건`
}
