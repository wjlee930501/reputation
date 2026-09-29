import { ApiError } from './api.ts'
import { safeOperatorError } from './operations-journey.ts'
import { isRecord } from './type-guards.ts'

// 발행 전 진료비·병원 선택 글에 검증된 문서 목록의 문서를 넣은 저장(PATCH 422)의 서버 코드.
export const CURATED_REFERENCE_NOT_ALLOWED = 'CURATED_REFERENCE_NOT_ALLOWED'

/**
 * 저장 실패가 목록 문서 거절(422)이면 서버의 안내 문장(넣을 수 없는 주소 포함)을, 아니면 null.
 *
 * 다른 저장 실패는 종전처럼 일반 안내로 바꾼다 — 이 코드의 문장만 운영자가 무엇을 고칠지
 * (어느 주소를 빼고 무엇을 넣을지) 알려 주므로 그대로 보여 준다.
 */
export function curatedReferenceRejectionMessage(error: unknown): string | null {
  if (!(error instanceof ApiError) || error.status !== 422 || !isRecord(error.detail)) return null
  if (error.detail.code !== CURATED_REFERENCE_NOT_ALLOWED) return null
  const message = typeof error.detail.message === 'string' ? error.detail.message.trim() : ''
  return message || null
}

/** 저장 실패(PATCH) 응답에 실린 금지 표현 목록. 없으면 빈 목록. */
export function readViolationsFromError(error: unknown): string[] {
  if (!(error instanceof ApiError) || !isRecord(error.detail)) return []
  const violations = error.detail.violations
  return Array.isArray(violations) ? violations.map((v) => String(v)) : []
}

export interface SaveEditFailure {
  readonly violations: string[]
  readonly message: string
}

/**
 * 편집 저장 실패를 화면에 보일 문장으로 바꾼다 — 금지 표현이 먼저, 그다음 목록 문서 거절(422)의
 * 서버 문장, 나머지는 일반 안내다.
 */
export function saveEditFailure(error: unknown): SaveEditFailure {
  const violations = readViolationsFromError(error)
  if (violations.length > 0) return { violations, message: `금지 표현: ${violations.join(', ')}` }
  const curatedRejection = curatedReferenceRejectionMessage(error)
  if (curatedRejection) {
    // 목록 문서 거절(422)은 어느 주소를 빼야 하는지 서버 문장이 말한다.
    return { violations, message: curatedRejection }
  }
  return {
    violations,
    message: safeOperatorError('content', '입력 내용을 확인한 뒤 ‘저장’을 다시 누르세요.'),
  }
}
