import { ApiError } from './api.ts'
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
