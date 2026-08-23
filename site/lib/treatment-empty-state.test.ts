import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import type { ContentSummary } from './api.ts'
import { buildTreatmentEmptyStatePaths } from './treatment-empty-state.ts'

const PAGE = readFileSync(
  join(
    dirname(fileURLToPath(import.meta.url)),
    '..',
    'app',
    '[slug]',
    'treatments',
    '[treatmentSlug]',
    'page.tsx',
  ),
  'utf8',
)

function content(id: string, publishedAt: string | null, scheduled: string): ContentSummary {
  return {
    id,
    content_type: 'DISEASE',
    title: `글 ${id}`,
    meta_description: null,
    image_url: null,
    scheduled_date: scheduled,
    published_at: publishedAt,
    body_updated_at: null,
    references: [],
    faq_question: null,
    faq_answer_summary: null,
  } as unknown as ContentSummary
}

test('the empty state points at the clinic other treatment areas', () => {
  const paths = buildTreatmentEmptyStatePaths({
    treatments: [{ name: '치질 수술' }, { name: '대장내시경' }, { name: '탈장 수술' }],
    currentTreatmentName: '대장내시경',
    contents: [],
  })

  assert.deepEqual(paths.siblings.map((t) => t.name), ['치질 수술', '탈장 수술'])
  assert.deepEqual(paths.recentContents, [])
})

test('treatments with no usable slug are not offered as links', () => {
  const paths = buildTreatmentEmptyStatePaths({
    treatments: [{ name: '  ' }, { name: '/' }, { name: '탈장 수술' }],
    currentTreatmentName: '치질 수술',
    contents: [],
  })

  assert.deepEqual(paths.siblings.map((t) => t.name), ['탈장 수술'])
})

test('the recent content list is newest first and capped at three', () => {
  const paths = buildTreatmentEmptyStatePaths({
    treatments: [],
    currentTreatmentName: '대장내시경',
    contents: [
      content('a', '2026-01-02', '2026-01-01'),
      content('b', null, '2026-03-01'),
      content('c', '2026-02-01', '2026-01-01'),
      content('d', '2025-12-01', '2025-12-01'),
    ],
  })

  assert.deepEqual(paths.recentContents.map((c) => c.id), ['b', 'c', 'a'])
})

test('at most six sibling areas are offered', () => {
  const paths = buildTreatmentEmptyStatePaths({
    treatments: Array.from({ length: 12 }, (_, i) => ({ name: `진료 ${i}` })),
    currentTreatmentName: '진료 0',
    contents: [],
  })

  assert.equal(paths.siblings.length, 6)
})

test('the page no longer ends at a single "준비 중" line', () => {
  // P-A-2 — 콘텐츠가 아직 없는 진료 영역 페이지는 sitemap·llms.txt에 실리는데도
  // 본문이 한 문장뿐이었다. 전화·방문·다른 진료 영역·이미 발행된 글로 이어져야 한다.
  const emptyBranch = PAGE.slice(
    PAGE.indexOf('relatedContents.length === 0'),
    PAGE.indexOf(') : ('),
  )
  assert.match(emptyBranch, /clinic-treatment-empty-actions/)
  assert.match(emptyBranch, /tel:\$\{hospital\.phone\}/)
  assert.match(emptyBranch, /\/visit/)
  assert.match(emptyBranch, /emptyStatePaths\.siblings/)
  assert.match(emptyBranch, /emptyStatePaths\.recentContents/)
})
