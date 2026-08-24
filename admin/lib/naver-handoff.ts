import { isRecord } from './type-guards.ts'

export type NaverHandoffState = 'PENDING' | 'INGESTED' | 'FAILED' | 'SKIPPED'

export interface NaverHandoffItem {
  url: string
  urlHash: string
  state: NaverHandoffState
  safeErrorCode: string | null
  safeErrorMessage: string | null
  nextAction: string | null
  sourceId: string | null
  retryOfRunId: string | null
  runId: string
}

export interface NaverHandoffResponse {
  operationRunId: string
  created: number
  skippedDuplicate: number
  skippedEmpty: number
  items: NaverHandoffItem[]
}

export interface NaverItemCopy {
  label: string
  impact: string
  action: string
  tone: 'success' | 'neutral' | 'warning' | 'danger'
}

export interface NaverDeveloperFacts {
  hospitalId: string
  runId: string
  urlHash: string
  state: NaverHandoffState
}

export class NaverHandoffParseError extends Error {
  constructor() {
    super('네이버 수집 결과를 확인할 수 없습니다. 다시 시도해 주세요.')
    this.name = 'NaverHandoffParseError'
  }
}

export function parseNaverHandoffResponse(value: unknown): NaverHandoffResponse {
  if (!isRecord(value)) throw new NaverHandoffParseError()
  const operationRunId = requiredString(value.operation_run_id)
  const items = Array.isArray(value.items)
    ? value.items.map((item) => parseItem(item, operationRunId))
    : null
  if (!items) throw new NaverHandoffParseError()
  return {
    operationRunId,
    created: requiredNumber(value.created),
    skippedDuplicate: requiredNumber(value.skipped_duplicate),
    skippedEmpty: requiredNumber(value.skipped_empty),
    items,
  }
}

export function parseNaverOpenFailures(value: unknown): NaverHandoffItem[] {
  if (!isRecord(value) || !Array.isArray(value.items)) throw new NaverHandoffParseError()
  return value.items.map((item) => {
    if (!isRecord(item)) throw new NaverHandoffParseError()
    return parseItem(item, requiredString(item.operation_run_id))
  })
}

export function naverItemCopy(item: NaverHandoffItem): NaverItemCopy {
  switch (item.state) {
    case 'PENDING':
      return {
        label: '가져오는 중',
        impact: '아직 근거 자료에 추가되지 않았습니다.',
        action: '잠시 후 현재 상태를 다시 확인해 주세요.',
        tone: 'neutral',
      }
    case 'INGESTED':
      return {
        label: '근거 자료에 추가됨',
        impact: '검토 대기 목록에 추가되었습니다.',
        action: '자료 내용을 확인한 뒤 운영 기준에 반영해 주세요.',
        tone: 'success',
      }
    case 'FAILED':
      return {
        label: '수집하지 못함',
        impact: '이 글은 아직 근거 자료에 추가되지 않았습니다. 다른 자료에는 영향이 없습니다.',
        action: '다시 수집을 눌러 주세요. 계속 실패하면 아래 정보를 복사해 개발팀에 문의해 주세요.',
        tone: 'danger',
      }
    case 'SKIPPED':
      return skippedCopy(item.safeErrorCode)
  }
}

export function isNaverEvidenceAvailable(item: NaverHandoffItem): boolean {
  return item.state === 'INGESTED'
    || (item.state === 'SKIPPED' && item.safeErrorCode === 'DUPLICATE_SOURCE')
}

export function buildNaverDeveloperContext(facts: NaverDeveloperFacts): string {
  return [
    '네이버 블로그 글 수집 문의',
    `병원 ID: ${facts.hospitalId}`,
    `작업 번호: ${facts.runId}`,
    `글 식별값: ${facts.urlHash}`,
    `화면 상태: ${stateLabel(facts.state)}`,
    '원문이나 환자 정보는 포함하지 않았습니다.',
  ].join('\n')
}

function stateLabel(state: NaverHandoffState): string {
  switch (state) {
    case 'PENDING':
      return '가져오는 중'
    case 'INGESTED':
      return '근거 자료에 추가됨'
    case 'FAILED':
      return '수집하지 못함'
    case 'SKIPPED':
      return '본문 확인 필요 또는 이미 가져온 글'
  }
}

function skippedCopy(code: string | null): NaverItemCopy {
  if (code === 'DUPLICATE_SOURCE') {
    return {
      label: '이미 가져온 글',
      impact: '기존 근거 자료를 그대로 유지하고 중복 저장하지 않았습니다.',
      action: '추가 조치가 필요하지 않습니다.',
      tone: 'neutral',
    }
  }
  return {
    label: '본문 확인 필요',
    impact: '본문을 확인할 수 없어 근거 자료에 추가하지 않았습니다.',
    action: '글이 공개되어 있는지 확인하세요. 필요한 글이라면 아래 정보를 복사해 개발팀에 문의해 주세요.',
    tone: 'warning',
  }
}

function parseItem(value: unknown, runId: string): NaverHandoffItem {
  if (!isRecord(value)) throw new NaverHandoffParseError()
  return {
    url: requiredString(value.url),
    urlHash: requiredString(value.url_hash),
    state: requiredState(value.state),
    safeErrorCode: optionalString(value.safe_error_code),
    safeErrorMessage: optionalString(value.safe_error_message),
    nextAction: optionalString(value.next_action),
    sourceId: optionalString(value.source_id),
    retryOfRunId: optionalString(value.retry_of_run_id),
    runId,
  }
}

function requiredState(value: unknown): NaverHandoffState {
  switch (value) {
    case 'PENDING':
    case 'INGESTED':
    case 'FAILED':
    case 'SKIPPED':
      return value
    default:
      throw new NaverHandoffParseError()
  }
}

function requiredString(value: unknown): string {
  if (typeof value !== 'string' || value.length === 0) throw new NaverHandoffParseError()
  return value
}

function optionalString(value: unknown): string | null {
  if (value === null || value === undefined) return null
  return requiredString(value)
}

function requiredNumber(value: unknown): number {
  if (typeof value !== 'number' || !Number.isInteger(value) || value < 0) {
    throw new NaverHandoffParseError()
  }
  return value
}
