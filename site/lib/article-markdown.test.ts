import assert from 'node:assert/strict'
import test from 'node:test'

import {
  articleHeadingId,
  extractArticleHeadings,
  stripLeadingMarkdownH1,
} from './article-markdown.ts'

test('removes only the generated leading H1', () => {
  const body = '# 반복 제목\n\n첫 문단입니다.\n\n## 진단 기준\n내용'
  assert.equal(stripLeadingMarkdownH1(body), '첫 문단입니다.\n\n## 진단 기준\n내용')
})

test('keeps ordinary body content and extracts navigable H2 headings', () => {
  const body = '첫 문단입니다.\n\n## 진단 기준\n내용\n\n### 세부 단계\n내용\n\n## 치료 방향\n내용'
  assert.equal(stripLeadingMarkdownH1(body), body)
  assert.deepEqual(extractArticleHeadings(body), [
    { id: 'section-진단-기준', label: '진단 기준' },
    { id: 'section-치료-방향', label: '치료 방향' },
  ])
  assert.equal(articleHeadingId('회복 & 주의사항'), 'section-회복-주의사항')
})
