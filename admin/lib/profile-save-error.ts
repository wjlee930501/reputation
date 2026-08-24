import { ApiError } from './api.ts'

const PROFILE_SAVE_FALLBACK =
  '병원 기본 정보를 저장하지 못했습니다. 입력값과 네트워크 상태를 확인한 뒤 다시 저장해 주세요.'

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function fieldLabel(value: unknown): string | null {
  if (typeof value === 'string' && value.trim()) return value.trim()
  if (!isRecord(value)) return null
  for (const key of ['field', 'key', 'label', 'message']) {
    const candidate = value[key]
    if (typeof candidate === 'string' && candidate.trim()) return candidate.trim()
  }
  return null
}

function missingFieldLabels(detail: Record<string, unknown>): string[] {
  for (const key of ['missing_fields', 'missing']) {
    const value = detail[key]
    const singleLabel = fieldLabel(value)
    if (singleLabel) return [singleLabel]
    if (!Array.isArray(value)) continue
    const labels = value.map(fieldLabel).filter((item): item is string => Boolean(item))
    if (labels.length > 0) return labels
  }
  return []
}

/** Preserve the backend's concrete profile-save reason instead of replacing it with onboarding copy. */
export function profileSaveErrorMessage(error: unknown): string {
  if (!(error instanceof ApiError)) {
    const reason = error instanceof Error ? error.message.trim() : ''
    return reason ? `${PROFILE_SAVE_FALLBACK}\n상세: ${reason}` : PROFILE_SAVE_FALLBACK
  }

  if (!isRecord(error.detail)) return error.message || PROFILE_SAVE_FALLBACK

  const detail = error.detail
  const message =
    typeof detail.message === 'string' && detail.message.trim()
      ? detail.message.trim()
      : error.message || PROFILE_SAVE_FALLBACK
  const code = typeof detail.code === 'string' && detail.code.trim() ? detail.code.trim() : null
  const missing = missingFieldLabels(detail)

  return [
    message,
    code ? `오류 코드: ${code}` : null,
    missing.length > 0 ? `확인할 항목: ${missing.join(', ')}` : null,
  ].filter((line): line is string => Boolean(line)).join('\n')
}
