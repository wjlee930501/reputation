/**
 * 담당자·검토자·확인자 이름을 화면에 쓸 수 있는 형태로 바꾼다.
 *
 * 이 값들은 사람 이름만 들어오는 칸이 아니다. 자동 발행 워커는
 * `SYSTEM_AUTO_PUBLISH`, 운영 기준 자동 검수는 `SYSTEM_ESSENCE_AI_REVIEW`를 남기고,
 * 과거 데이터 보정 작업은 `bulk-restore-...` 같은 내부 배치 식별자를 남겼다.
 * 화면은 그 값을 그대로 찍어서 "검토자 bulk-restore-2026-07-19"처럼 읽혔다 —
 * 운영자는 그게 사람인지 시스템인지, 그 승인을 신뢰해도 되는지 알 수 없다.
 *
 * 그래서 (1) 아는 시스템 행위자는 무엇이 한 일인지 이름으로 알리고,
 * (2) 사람 이름일 수 없는 내부 식별자는 사람 이름 자리에 찍지 않는다.
 */

export type ActorKind =
  /** 로그인한 운영자 */
  | 'HUMAN'
  /** AI 시스템 검수자가 정상 자동 승인했다 */
  | 'AI'
  /** 사람 판단 없이 자동 실행된 시스템 작업 */
  | 'SYSTEM'
  /** 사람 이름으로 볼 수 없는 내부 식별자 */
  | 'INTERNAL'
  /** 값이 없다 */
  | 'UNKNOWN'

export interface ActorDisplay {
  kind: ActorKind
  label: string
  /** AI·시스템 자동 처리를 운영자 override와 구분하기 위한 표시 */
  isAutomated: boolean
}

/** 백엔드가 실제로 저장하는 시스템 행위자 값. */
const KNOWN_SYSTEM_ACTORS: Record<string, { label: string; kind: 'AI' | 'SYSTEM' }> = {
  SYSTEM_ESSENCE_AI_REVIEW: { label: 'AI 시스템 자동 승인', kind: 'AI' },
  SYSTEM_AUTO_PUBLISH: { label: '자동 발행', kind: 'SYSTEM' },
  SYSTEM_EXPOSURE_PLANNER: { label: '자동 작업 편성', kind: 'SYSTEM' },
  SYSTEM_RECURSIVE_LEARNING: { label: '자동 학습 반영', kind: 'SYSTEM' },
  SYSTEM_MANUAL_RECOVERY: { label: '수동 복구 작업', kind: 'SYSTEM' },
  NAVER_WEEKLY_SYNC: { label: '자료 주간 수집', kind: 'SYSTEM' },
}

/**
 * 사람 이름일 수 없는 내부 식별자 판별.
 *
 * `bulk-restore-9f2c`, `backfill_2026_07`, `migration-0042`처럼 배치 작업이 남긴
 * 값과, 그냥 UUID가 들어온 값을 잡는다. 한글·공백이 있으면 사람 이름으로 본다.
 */
const INTERNAL_TOKEN_PREFIXES = ['bulk-', 'bulk_', 'backfill', 'migration', 'seed-', 'seed_', 'sync-', 'job-', 'task-']
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

function looksLikeInternalToken(value: string): boolean {
  const lowered = value.toLowerCase()
  if (UUID_RE.test(lowered)) return true
  if (/[가-힣]/.test(value)) return false
  if (INTERNAL_TOKEN_PREFIXES.some((prefix) => lowered.startsWith(prefix))) return true
  // ALL_CAPS_WITH_UNDERSCORES는 코드 상수 형태다 — 사람 이름으로 저장되지 않는다.
  if (/^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+$/.test(value)) return true
  return false
}

export function describeActor(raw: string | null | undefined): ActorDisplay {
  const value = (raw ?? '').trim()
  if (!value) {
    return { kind: 'UNKNOWN', label: '확인 필요', isAutomated: false }
  }

  const known = KNOWN_SYSTEM_ACTORS[value.toUpperCase()]
  if (known) {
    return { kind: known.kind, label: known.label, isAutomated: true }
  }

  if (looksLikeInternalToken(value)) {
    return { kind: 'INTERNAL', label: '시스템 자동 처리', isAutomated: true }
  }

  return { kind: 'HUMAN', label: value, isAutomated: false }
}

/** 한 줄 표시용 — 알려진 시스템 행위자의 정상 자동 처리명을 그대로 쓴다. */
export function formatActorLabel(raw: string | null | undefined): string {
  return describeActor(raw).label
}
