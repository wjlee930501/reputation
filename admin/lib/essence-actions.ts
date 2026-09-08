/**
 * 예외 초안 하나에 사람이 할 수 있는 세 가지 — 초안 편집 저장, 자료 기준 자동 재검수,
 * 예외 승인 — 의 요청과 응답 해석.
 *
 * 현황 화면의 예외 카드와 운영 기준 화면이 같은 요청을 보내야 한다. 화면마다 몸통을
 * 새로 지으면 한쪽만 필드가 빠지거나 확인 문구가 달라진다.
 */
import { ApiError } from './api.ts'
import { isRecord } from './type-guards.ts'
import type { ContentPhilosophy } from '../types/index.ts'

/** 재합성 입력은 자료와 근거 노트다. 초안에 직접 고친 문장은 반영되지 않는다. */
export const RE_REVIEW_CONFIRM =
  '자료·근거 노트를 기준으로 콘텐츠 운영 기준을 다시 합성·검수합니다(유료 AI 호출). 초안에 직접 고친 문장은 반영되지 않습니다. 계속하시겠습니까?'

/** 초안 편집 칸의 값. 목록 항목은 줄바꿈으로 나눈 한 덩어리 글로 다룬다. */
export interface PhilosophyDraftText {
  positioning: string
  voice: string
  promise: string
  principles: string
  tone: string
  mustUse: string
  avoid: string
  riskRules: string
}

function listToText(values: unknown): string {
  return Array.isArray(values) ? values.map((item) => String(item)).join('\n') : ''
}

function textToList(value: string): string[] {
  return value
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
}

export function philosophyDraftText(philosophy: ContentPhilosophy): PhilosophyDraftText {
  return {
    positioning: philosophy.positioning_statement ?? '',
    voice: philosophy.doctor_voice ?? '',
    promise: philosophy.patient_promise ?? '',
    principles: listToText(philosophy.content_principles),
    tone: listToText(philosophy.tone_guidelines),
    mustUse: listToText(philosophy.must_use_messages),
    avoid: listToText(philosophy.avoid_messages),
    riskRules: listToText(philosophy.medical_ad_risk_rules),
  }
}

/** `PhilosophyPatch`가 받는 여덟 칸. 빈 글은 null로 보내 "지웠다"를 분명히 한다. */
export function philosophyPatchBody(draft: PhilosophyDraftText): Record<string, unknown> {
  return {
    positioning_statement: draft.positioning || null,
    doctor_voice: draft.voice || null,
    patient_promise: draft.promise || null,
    content_principles: textToList(draft.principles),
    tone_guidelines: textToList(draft.tone),
    must_use_messages: textToList(draft.mustUse),
    avoid_messages: textToList(draft.avoid),
    medical_ad_risk_rules: textToList(draft.riskRules),
  }
}

export interface PhilosophyApprovalInput {
  reviewedBy: string
  approvalNote: string
  confirmEvidence: boolean
  overrideReason: string
}

export function philosophyApproveBody(input: PhilosophyApprovalInput): Record<string, unknown> {
  return {
    reviewed_by: input.reviewedBy,
    approval_note: input.approvalNote || null,
    confirm_evidence_reviewed: input.confirmEvidence,
    override_reason: input.overrideReason.trim() || null,
  }
}

/**
 * 재검수 요청 결과 안내. `re_review_dispatched === false`면 보관까지만 끝났고 검수는
 * 다음 자동 재조정이 회수한다 — 곧 결과가 온다고 말하지 않는다.
 */
export function reReviewNotice(result: Pick<ContentPhilosophy, 're_review_dispatched'>): string {
  return result.re_review_dispatched === false
    ? '초안을 보관했습니다. 자동 재검수는 다음 자동 재조정에서 시작됩니다(15분 주기, 병원 200곳 단위로 순환하므로 병원 수에 따라 더 걸릴 수 있습니다).'
    : '초안을 보관하고 자동 검수를 다시 요청했습니다. 결과는 이 화면과 운영 센터에 표시됩니다.'
}

function detailCode(error: unknown): { code: string; detail: Record<string, unknown> } | null {
  if (!(error instanceof ApiError) || !isRecord(error.detail)) return null
  const code = error.detail.code
  return typeof code === 'string' ? { code, detail: error.detail } : null
}

/**
 * 30분 쿨다운(429)은 실패가 아니라 "아직 앞 요청이 돌고 있다"는 뜻이다. 남은 시간을
 * 함께 말해 주지 않으면 운영자가 계속 다시 누른다.
 */
export function reReviewErrorMessage(error: unknown): string {
  const known = detailCode(error)
  if (known?.code === 'RE_REVIEW_COOLDOWN') {
    const seconds = known.detail.retry_after_seconds
    const minutes = typeof seconds === 'number' ? Math.max(1, Math.ceil(seconds / 60)) : null
    const wait = minutes === null ? '' : ` 약 ${minutes}분 뒤에 다시 요청할 수 있습니다.`
    return `${(error as ApiError).message}${wait}`
  }
  return error instanceof Error ? error.message : '재검수 요청에 실패했습니다.'
}

/**
 * 승인 거절 사유. 자동 검수가 보류한 사유(409)와 근거 검증 실패(422)는 화면이
 * 그대로 보여 준다 — 일반 오류 문구로 뭉개면 무엇을 고쳐야 하는지 사라진다.
 */
export function approveErrorMessage(error: unknown): string {
  const known = detailCode(error)
  if (known?.code === 'AUTO_REVIEW_FINDINGS_UNRESOLVED') {
    const findings = Array.isArray(known.detail.findings)
      ? known.detail.findings.map((finding) => String(finding))
      : []
    const message = (error as ApiError).message
    return findings.length > 0 ? `${message}\n- ${findings.join('\n- ')}` : message
  }
  return error instanceof Error ? error.message : '승인에 실패했습니다.'
}
