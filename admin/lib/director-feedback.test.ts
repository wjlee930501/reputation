import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const component = readFileSync(new URL('../app/hospitals/[id]/reports/DirectorFeedback.tsx', import.meta.url), 'utf8')
const page = readFileSync(new URL('../app/hospitals/[id]/reports/page.tsx', import.meta.url), 'utf8')

test('doctor conversation lives inside reports and sends hospital-scoped preferences', () => {
  assert.match(page, /<DirectorFeedback hospitalId=\{hospitalId\}/)
  assert.match(component, /admin\/hospitals\/\$\{hospitalId\}\/director-feedback/)
  for (const field of ['prefer_topics', 'prefer_messages', 'avoid_messages', 'conversation_reference']) assert.ok(component.includes(field))
  assert.match(component, /기존 글은 자동으로 다시 만들지 않습니다/)
  assert.match(component, /안전 기준이 항상 우선/)
  assert.match(component, /\$\{path\}\/\$\{id\}\/retire/)
  assert.doesNotMatch(component, /regenerate|generate-content|review-content/)
})

test('feedback form labels bounded inputs and prevents duplicate clicks while saving', () => {
  assert.match(component, /maxLength=\{500\}/)
  assert.match(component, /disabled=\{busy/)
  assert.match(component, /role="status"/)
  assert.match(component, /<label>관심 주제/)
  assert.match(component, /<label>피할 표현/)
})
