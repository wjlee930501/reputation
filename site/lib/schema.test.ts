import assert from 'node:assert/strict'
import test from 'node:test'

import { buildFaqPageJsonLd } from './schema.ts'

function faq(id: number) {
  return {
    id: String(id),
    content_type: 'FAQ',
    title: `FAQ ${id}`,
    meta_description: `Answer ${id}`,
    image_url: null,
    scheduled_date: '2026-06-20',
    published_at: null,
    body_updated_at: null,
    references: [],
    faq_question: `Question ${id}`,
    faq_answer_summary: `Answer ${id}`,
  }
}

test('production FAQPage JSON-LD caps mainEntity at 10 entries', () => {
  const result = buildFaqPageJsonLd(
    Array.from({ length: 15 }, (_, i) => faq(i)),
    'https://clinic.example',
    'demo-clinic',
  )

  assert.ok(result)
  const mainEntity = result.mainEntity
  assert.ok(Array.isArray(mainEntity))
  assert.equal(mainEntity.length, 10)
})
