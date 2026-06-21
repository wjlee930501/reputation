import assert from 'node:assert/strict'
import test from 'node:test'

import { buildAddressRegionFields, buildFaqPageJsonLd } from './clinic-schema.ts'

function faq(faq_question: string | null, faq_answer_summary: string | null) {
  return { content_type: 'FAQ', faq_question, faq_answer_summary }
}

test('buildFaqPageJsonLd returns null when there are no FAQ contents', () => {
  assert.equal(buildFaqPageJsonLd([]), null)
  assert.equal(buildFaqPageJsonLd(null), null)
  assert.equal(
    buildFaqPageJsonLd([{ content_type: 'DISEASE', faq_question: 'q', faq_answer_summary: 'a' }]),
    null,
  )
})

test('buildFaqPageJsonLd skips FAQ entries missing question or answer', () => {
  assert.equal(buildFaqPageJsonLd([faq(null, 'a'), faq('q', null), faq('', '')]), null)
})

test('buildFaqPageJsonLd aggregates valid FAQ entries into mainEntity', () => {
  const result = buildFaqPageJsonLd([
    faq('무릎이 아플 때 어떻게 하나요?', '냉찜질 후 통증이 지속되면 진료를 권합니다.'),
    { content_type: 'DISEASE', faq_question: 'x', faq_answer_summary: 'y' },
    faq('수술 후 회복 기간은?', '개인차가 있으나 보통 2주 정도입니다.'),
  ])
  assert.deepEqual(result, {
    '@context': 'https://schema.org',
    '@type': 'FAQPage',
    mainEntity: [
      {
        '@type': 'Question',
        name: '무릎이 아플 때 어떻게 하나요?',
        acceptedAnswer: { '@type': 'Answer', text: '냉찜질 후 통증이 지속되면 진료를 권합니다.' },
      },
      {
        '@type': 'Question',
        name: '수술 후 회복 기간은?',
        acceptedAnswer: { '@type': 'Answer', text: '개인차가 있으나 보통 2주 정도입니다.' },
      },
    ],
  })
})

test('buildFaqPageJsonLd caps mainEntity at 10 entries', () => {
  const many = Array.from({ length: 15 }, (_, i) => faq(`q${i}`, `a${i}`))
  const result = buildFaqPageJsonLd(many)
  assert.ok(result)
  assert.equal((result!.mainEntity as unknown[]).length, 10)
})

test('buildAddressRegionFields maps region[0]→region, region[1]→locality', () => {
  assert.deepEqual(buildAddressRegionFields(['서울', '강남구']), {
    addressRegion: '서울',
    addressLocality: '강남구',
  })
})

test('buildAddressRegionFields omits missing parts and trims', () => {
  assert.deepEqual(buildAddressRegionFields(['  서울  ']), { addressRegion: '서울' })
  assert.deepEqual(buildAddressRegionFields([]), {})
  assert.deepEqual(buildAddressRegionFields(null), {})
  assert.deepEqual(buildAddressRegionFields(['', '강남구']), { addressRegion: '강남구' })
})
