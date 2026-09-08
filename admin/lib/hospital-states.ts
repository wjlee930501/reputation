/**
 * 서버가 판정한 병원 3상태(공개 서비스·콘텐츠 준비·자기 도메인)를 화면 문구로만 바꾼다.
 *
 * 판정은 backend `services/hospital_states.py` 한 곳에서만 한다 — 목록·헤더·현황이
 * 각자 판정하면 같은 병원이 화면마다 다른 상태로 보인다(PR-0A H-06).
 *
 * 남은 조건은 사람 몫과 시스템 몫을 나눠 말한다. 자동으로 진행 중인 일을 운영자의
 * 할 일처럼 적으면 손댈 수 없는 항목이 계속 할 일 목록에 남는다.
 */
import { domainLastCheckedLabel } from './hospital-domain-status.ts'

export type PublicServiceKind = 'live' | 'paused' | 'not_live'
export type ContentKind = 'auto' | 'preparing' | 'exception'
export type DomainKind = 'connected' | 'checking' | 'problem' | 'unused'
export type StateKind = PublicServiceKind | ContentKind | DomainKind
export type StateTone = 'good' | 'neutral' | 'warn' | 'paused'

/** `overview` 응답이 주는 남은 조건 하나. 목록 응답은 같은 조건을 키 문자열로만 준다. */
export interface RemainingCondition {
  key: string
  label: string
  actor: 'human' | 'system'
  href: string | null
}

/** 목록의 `string[]`과 `overview`의 조건 객체를 같은 함수가 받는다. */
export type RemainingInput = ReadonlyArray<string | RemainingCondition>

export interface PublicServiceStateValue {
  kind: PublicServiceKind
  remaining: RemainingInput
}

export interface ContentStateValue {
  kind: ContentKind
  remaining: RemainingInput
}

export interface DomainStateValue {
  kind: DomainKind
  reason?: string | null
  last_checked_at?: string | null
  /** 마지막 관측의 성패. 없으면 "마지막 확인 …"만 말하고 정상/실패를 붙이지 않는다. */
  last_check_ok?: boolean | null
}

export interface StateDescription {
  label: string
  detail: string | null
}

const PUBLIC_SERVICE_LABELS: Record<PublicServiceKind, string> = {
  live: '공개 중',
  paused: '일시 정지',
  not_live: '준비 중',
}

const CONTENT_LABELS: Record<ContentKind, string> = {
  auto: '자동 발행 중',
  preparing: '준비 중',
  exception: '예외 있음',
}

const DOMAIN_LABELS: Record<DomainKind, string> = {
  connected: '연결됨',
  checking: '확인 중',
  problem: '문제',
  unused: '사용 안 함',
}

// 목록 응답의 조건 키 → 문구. `overview`는 같은 표를 서버에서 이미 적용해 내려주므로
// (`api/admin/hospital_overview.py`의 `_CONDITIONS`) 두 문구는 같아야 한다.
const CONDITION_LABELS: Record<string, string> = {
  profile_complete: '필수 병원 정보 입력',
  site_built: '공개 페이지 준비',
  schedule: '발행 요일 설정',
  sources: '근거 자료 처리 {count}건',
  essence_review: '콘텐츠 운영 기준 자동 검수',
  // 자료가 하나도 없으면 자동 검수는 기다리기만 한다 — 사람이 채워야 다음이 있다.
  sources_required: '공식 채널·근거 자료 등록',
  // 공개 서비스 중이 아닌 병원은 자동 발행이 돌지 않는다.
  service_paused: '서비스 재개',
  public_service: '공개 서비스 시작 후 자동 발행',
}

const HUMAN_CONDITIONS = new Set([
  'profile_complete',
  'schedule',
  'sources_required',
  'service_paused',
])

/** `sources:2` 같은 목록 조건 키를 사람이 읽는 문구로. */
export function remainingConditionLabel(key: string): string {
  const [name, count] = key.split(':')
  const label = CONDITION_LABELS[name]
  // 서버가 조건을 늘렸는데 화면이 빈칸이 되면 무엇이 남았는지 알 수 없다 — 키를 그대로 보인다.
  if (!label) return key
  return label.replace('{count}', count ?? '')
}

function toCondition(item: string | RemainingCondition): RemainingCondition {
  if (typeof item !== 'string') return item
  const [name] = item.split(':')
  return {
    key: item,
    label: remainingConditionLabel(item),
    actor: HUMAN_CONDITIONS.has(name) ? 'human' : 'system',
    // 목록 행은 이동 경로를 받지 않는다. 링크는 `overview`의 사람 몫 조건에만 붙는다.
    href: null,
  }
}

/** 사람이 손대야 하는 조건만. 헤더가 여기에만 링크를 붙인다. */
export function humanRemaining(remaining: RemainingInput): RemainingCondition[] {
  return remaining.map(toCondition).filter((condition) => condition.actor === 'human')
}

function describeRemaining(remaining: RemainingInput): string | null {
  const conditions = remaining.map(toCondition)
  const human = conditions.filter((c) => c.actor === 'human').map((c) => c.label)
  const system = conditions.filter((c) => c.actor === 'system').map((c) => c.label)
  const parts: string[] = []
  if (human.length > 0) parts.push(`할 일: ${human.join(' · ')}`)
  if (system.length > 0) parts.push(`시스템 처리 중: ${system.join(' · ')}`)
  return parts.length > 0 ? parts.join(' · ') : null
}

export function describePublicService(state: PublicServiceStateValue): StateDescription {
  return {
    label: PUBLIC_SERVICE_LABELS[state.kind] ?? '상태 확인 필요',
    detail: describeRemaining(state.remaining),
  }
}

export function describeContentState(state: ContentStateValue): StateDescription {
  return {
    label: CONTENT_LABELS[state.kind] ?? '상태 확인 필요',
    detail: describeRemaining(state.remaining),
  }
}

/**
 * 자기 도메인을 쓰지 않는 병원에는 아무 말도 하지 않는다 — 기본 주소로 이미 서비스
 * 중인데 도메인 칸이 '준비 중'이면 살아 있는 주소가 없는 것처럼 보인다(O-7).
 */
export function describeDomainState(state: DomainStateValue): StateDescription | null {
  if (state.kind === 'unused') return null
  const label =
    state.kind === 'problem'
      ? `${DOMAIN_LABELS.problem}: ${state.reason || '원인 확인 필요'}`
      : DOMAIN_LABELS[state.kind]
  return { label, detail: domainLastCheckedLabel(state.last_checked_at, state.last_check_ok) }
}

export function stateTone(kind: StateKind): StateTone {
  switch (kind) {
    case 'live':
    case 'auto':
    case 'connected':
      return 'good'
    case 'paused':
      return 'paused'
    case 'exception':
    case 'problem':
      return 'warn'
    default:
      return 'neutral'
  }
}
