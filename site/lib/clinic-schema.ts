// 병원 허브 JSON-LD 빌더 공통 유틸 — page.tsx가 쓰는 순수 함수만 모은다.
// (테스트 가능하도록 DOM/네트워크 의존 없이 입력→출력만 다룬다.)

import type { ContentSummary } from './api.ts'

interface ContentLike {
  content_type: ContentSummary['content_type']
  faq_question: ContentSummary['faq_question']
  faq_answer_summary: ContentSummary['faq_answer_summary']
}

const FAQ_MAX_ITEMS = 10

/**
 * 허브에 노출할 FAQPage JSON-LD를 만든다.
 * content_type === 'FAQ' 이면서 question/answer가 모두 있는 콘텐츠만 mainEntity로 모으고,
 * 상위 FAQ_MAX_ITEMS개로 제한한다. 대상이 없으면 null — 호출부는 빈 FAQPage를 내보내지 않는다.
 */
export function buildFaqPageJsonLd(
  contents: ContentLike[] | null | undefined,
): Record<string, unknown> | null {
  const mainEntity = (contents || [])
    .filter((c) => c.content_type === 'FAQ' && c.faq_question && c.faq_answer_summary)
    .slice(0, FAQ_MAX_ITEMS)
    .map((c) => ({
      '@type': 'Question',
      name: c.faq_question as string,
      acceptedAnswer: { '@type': 'Answer', text: c.faq_answer_summary as string },
    }))
  if (mainEntity.length === 0) return null
  return {
    '@context': 'https://schema.org',
    '@type': 'FAQPage',
    mainEntity,
  }
}

/**
 * hospital.region([시/도, 구/시])로 PostalAddress를 보강한다.
 * region[0] → addressRegion, region[1] → addressLocality. 비어 있으면 해당 키를 생략한다.
 * streetAddress/addressCountry는 호출부가 채운다 (이 함수는 지역 파생값만 더한다).
 */
export function buildAddressRegionFields(
  region: string[] | null | undefined,
): { addressRegion?: string; addressLocality?: string } {
  const cleaned = (region || []).map((r) => (r || '').trim()).filter(Boolean)
  const fields: { addressRegion?: string; addressLocality?: string } = {}
  if (cleaned[0]) fields.addressRegion = cleaned[0]
  if (cleaned[1]) fields.addressLocality = cleaned[1]
  return fields
}
