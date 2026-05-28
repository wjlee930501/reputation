// 콘텐츠 유형별 카테고리 태그 색상 + 읽기 시간 — 매거진형 카드 UI 공통 유틸.

// 콘텐츠 유형 → CSS 카테고리 태그 modifier. 색상은 globals.css의 .clinic-tag--* 에 정의.
const TAG_CLASS_BY_TYPE: Record<string, string> = {
  FAQ: 'clinic-tag--faq',
  DISEASE: 'clinic-tag--disease',
  TREATMENT: 'clinic-tag--treatment',
  COLUMN: 'clinic-tag--column',
  HEALTH: 'clinic-tag--health',
  LOCAL: 'clinic-tag--local',
  NOTICE: 'clinic-tag--notice',
}

export function categoryTagClass(contentType: string): string {
  return TAG_CLASS_BY_TYPE[contentType] ?? 'clinic-tag--faq'
}

// 한국어 평균 읽기 속도 약 500자/분 기준의 대략적인 읽기 시간(분).
export function readingMinutes(body: string | null | undefined): number {
  const chars = (body || '').replace(/\s+/g, '').length
  if (chars === 0) return 1
  return Math.max(1, Math.round(chars / 500))
}
