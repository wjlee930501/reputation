import assert from 'node:assert/strict'
import test from 'node:test'

import { faqItems } from './landing-copy.ts'
import {
  buildLandingFaqJsonLd,
  buildOrganizationJsonLd,
  buildServiceJsonLd,
  buildWebSiteJsonLd,
  MOTIONLABS,
} from './landing-schema.ts'

const SITE = 'https://reputation.motionlabs.kr'

test('조직 노드는 검증 가능한 사업자 정보를 담는다', () => {
  const org = buildOrganizationJsonLd(SITE) as Record<string, never>
  assert.equal(org['@type'], 'Organization')
  // 사업자등록번호가 빠지면 이 노드를 넣는 이유의 절반이 없어진다(E-E-A-T 신호).
  assert.equal(org.taxID, MOTIONLABS.taxId)
  assert.match(String(org.taxID), /^\d{3}-\d{2}-\d{5}$/)
})

test('노드들이 같은 @id로 서로를 참조한다', () => {
  /**
   * 노드가 서로를 못 가리키면 조직·서비스·사이트가 각각 떠 있는 조각이 된다.
   * 그래프로 묶여야 "이 서비스는 이 회사가 운영한다"가 기계에 전달된다.
   */
  const orgId = (buildOrganizationJsonLd(SITE) as Record<string, string>)['@id']
  const service = buildServiceJsonLd(SITE) as Record<string, { '@id': string }>
  const site = buildWebSiteJsonLd(SITE) as Record<string, { '@id': string }>
  assert.equal(service.provider['@id'], orgId)
  assert.equal(site.publisher['@id'], orgId)
})

test('끝 슬래시가 있어도 @id가 갈라지지 않는다', () => {
  const a = (buildOrganizationJsonLd(SITE) as Record<string, string>)['@id']
  const b = (buildOrganizationJsonLd(`${SITE}/`) as Record<string, string>)['@id']
  assert.equal(a, b)
})

test('FAQ 구조화 데이터는 화면의 질문을 전부 담는다', () => {
  /**
   * 화면에 11개가 있는데 구조화 데이터에 4개만 넣으면, 접힌 질문은 AI가 못 본다.
   * 반대로 화면에 없는 질문을 여기에만 넣으면 본문과 어긋나 스팸 신호가 된다.
   * 양쪽 다 막으려면 개수가 정확히 같아야 한다.
   */
  const faq = buildLandingFaqJsonLd(faqItems, SITE) as { mainEntity: unknown[] }
  assert.equal(faq.mainEntity.length, faqItems.length)
})

test('FAQ 각 항목이 질문과 답을 모두 갖는다', () => {
  const faq = buildLandingFaqJsonLd(faqItems, SITE) as {
    mainEntity: { name: string; acceptedAnswer: { text: string } }[]
  }
  for (const entry of faq.mainEntity) {
    assert.ok(entry.name.length > 0)
    assert.ok(entry.acceptedAnswer.text.length > 0)
  }
})

test('빈 질문·답은 걸러지고, 전부 비면 노드를 만들지 않는다', () => {
  const mixed = buildLandingFaqJsonLd(
    [
      { question: '진짜 질문', answer: '진짜 답' },
      { question: '  ', answer: '답만 있음' },
      { question: '질문만 있음', answer: '' },
    ],
    SITE,
  ) as { mainEntity: unknown[] }
  assert.equal(mixed.mainEntity.length, 1)
  assert.equal(buildLandingFaqJsonLd([], SITE), null)
})

test('가격을 구조화 데이터에만 몰래 넣지 않는다', () => {
  /**
   * 랜딩은 가격을 공개하지 않는다. `offers`를 넣으면 화면에 없는 값을 기계에만
   * 말하는 셈이고, 그 순간 이 페이지의 근거 규율이 깨진다.
   */
  const service = JSON.stringify(buildServiceJsonLd(SITE))
  assert.doesNotMatch(service, /"offers"|"price"/)
})
