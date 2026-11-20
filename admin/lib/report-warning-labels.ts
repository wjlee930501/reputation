/**
 * 보고서 주의·차단 문구 중 서버가 코드 그대로 남긴 값을 화면 문구로 바꾼다.
 *
 * 대부분의 경고는 서버가 한국어 문장으로 만들지만, 측정표 판정이 남긴 차단 코드
 * (`monthly_manifest.summarize_manifest`)와 0041 마이그레이션이 옛 보고서에 넣은
 * `LEGACY_REPORT_UNVERIFIED`는 저장된 값을 그대로 내려준다. 화면에 영문 코드를 찍지 않는다.
 */

import { ADMIN_COPY } from './admin-copy.ts'

const CODE_SHAPE = /^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+$/

/** 코드면 화면 문구로, 이미 문장이면 그대로. 같은 문구가 겹치면 하나만 남긴다. */
export function reportNoticeLabels(values: readonly string[]): string[] {
  const labels = values.map((value) => {
    const trimmed = value.trim()
    if (!CODE_SHAPE.test(trimmed)) return value
    return ADMIN_COPY.reportNoticeCodes[trimmed] ?? ADMIN_COPY.reportNoticeUnknown
  })
  return Array.from(new Set(labels))
}
