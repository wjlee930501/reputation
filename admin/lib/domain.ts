// 커스텀 도메인 저장/검증 오류 해석 헬퍼.
// backend 계약:
//   - 도메인 저장: 422 → 잘못된 호스트네임 (한국어 메시지), 409 → 다른 병원이 이미 사용 중
//   - 도메인 검증(POST /domain/verify): 409 → DNS는 정상이나 운영 시작 전 선행 단계 미완료 (detail에 단계 목록)
import { ApiError } from './api.ts'

export type DomainErrorKind = 'invalid' | 'conflict' | 'prerequisite' | 'generic'

export interface DomainErrorInfo {
  kind: DomainErrorKind
  message: string
  missingSteps: string[]
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function toStepLabel(entry: unknown): string {
  if (typeof entry === 'string') return entry
  if (isRecord(entry)) {
    if (typeof entry.label === 'string') return entry.label
    if (typeof entry.message === 'string') return entry.message
    if (typeof entry.title === 'string') return entry.title
  }
  return ''
}

function readStringList(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  return value.map(toStepLabel).filter(Boolean)
}

/** 409 detail에서 미완료 선행 단계 목록을 찾아낸다. 알려진 키 이름을 순서대로 시도. */
export function extractMissingSteps(detail: unknown): string[] {
  if (Array.isArray(detail)) return readStringList(detail)
  if (!isRecord(detail)) return []
  for (const key of ['missing', 'missing_steps', 'prerequisites', 'prerequisite_steps', 'steps', 'checklist']) {
    const list = readStringList(detail[key])
    if (list.length > 0) return list
  }
  return []
}

/**
 * detail이 구조화 목록 없이 "...단계가 남아 있습니다: V0 리포트, 콘텐츠 스케줄"처럼
 * 문자열 한 줄로 내려오는 경우, 콜론 뒤 항목을 체크리스트로 분해한다.
 */
export function parseStepsFromMessage(message: string): string[] {
  const match = /[:：]\s*([^:：]+)$/.exec(message)
  if (!match) return []
  return match[1]
    .split(/[,·]/)
    .map((step) => step.replace(/\.$/, '').trim())
    .filter(Boolean)
}

/**
 * 도메인 저장/검증 오류를 화면에서 구분 표시할 수 있는 형태로 변환한다.
 * - 422 → invalid (형식 오류, backend 한국어 메시지 그대로)
 * - 409 + 단계 목록 → prerequisite (검증 전 선행 단계 미완료)
 * - 409 (목록 없음) → conflict (다른 병원이 이미 사용 중)
 */
export function readDomainError(error: unknown, fallback: string): DomainErrorInfo {
  if (!(error instanceof ApiError)) {
    return {
      kind: 'generic',
      message: error instanceof Error ? error.message : fallback,
      missingSteps: [],
    }
  }
  if (error.status === 422) {
    return { kind: 'invalid', message: error.message, missingSteps: [] }
  }
  if (error.status === 409) {
    const missingSteps = extractMissingSteps(error.detail)
    if (missingSteps.length > 0) {
      return { kind: 'prerequisite', message: error.message, missingSteps }
    }
    return { kind: 'conflict', message: error.message, missingSteps: [] }
  }
  return { kind: 'generic', message: error.message, missingSteps: [] }
}
