import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import { describeActor, formatActorLabel } from './actor-display.ts'

test('a batch job identifier never appears where an operator name belongs', () => {
  for (const token of [
    'bulk-restore-9f2c',
    'bulk-restore-2026-07-19',
    'bulk_restore_photos',
    'backfill-provenance',
    'migration-0052',
    'seed-demo',
    '3f2a9d4e-1c1b-4f0a-9b77-2b1d5a8e77aa',
  ]) {
    const actor = describeActor(token)
    assert.equal(actor.kind, 'INTERNAL', token)
    assert.equal(actor.label, '시스템 자동 처리', token)
    assert.equal(actor.isAutomated, true, token)
  }
})

test('the AI reviewer is labelled as AI, and never as a person who approved', () => {
  const actor = describeActor('SYSTEM_ESSENCE_AI_REVIEW')

  assert.equal(actor.kind, 'AI')
  assert.equal(actor.label, 'AI 자동 검수')
  assert.equal(formatActorLabel('SYSTEM_ESSENCE_AI_REVIEW'), 'AI 자동 검수 (사람 승인 아님)')
})

test('other system actors keep their own name so the operator knows what ran', () => {
  assert.equal(describeActor('SYSTEM_AUTO_PUBLISH').label, '자동 발행')
  assert.equal(describeActor('SYSTEM_EXPOSURE_PLANNER').label, '자동 작업 편성')
  assert.equal(describeActor('NAVER_WEEKLY_SYNC').label, '자료 주간 수집')
  assert.equal(describeActor('SYSTEM_AUTO_PUBLISH').kind, 'SYSTEM')
})

test('an unknown ALL_CAPS constant is treated as internal, not as a name', () => {
  const actor = describeActor('SYSTEM_SOMETHING_NEW')

  assert.equal(actor.kind, 'INTERNAL')
  assert.equal(actor.label, '시스템 자동 처리')
})

test('operator names pass through untouched, including Korean and English names', () => {
  assert.deepEqual(describeActor('이지은'), { kind: 'HUMAN', label: '이지은', isAutomated: false })
  assert.equal(describeActor('MotionLabs Ops').kind, 'HUMAN')
  assert.equal(describeActor('  이지은  ').label, '이지은')
  assert.equal(formatActorLabel('이지은'), '이지은')
})

test('a Korean name is never mistaken for a batch token', () => {
  // 한글이 있으면 접두어 규칙에 걸리지 않는다.
  assert.equal(describeActor('bulk-이지은').kind, 'HUMAN')
})

test('an empty value asks for confirmation instead of printing a dash as a name', () => {
  for (const empty of [null, undefined, '', '   ']) {
    const actor = describeActor(empty)
    assert.equal(actor.kind, 'UNKNOWN')
    assert.equal(actor.label, '확인 필요')
  }
})

test('every screen that shows a confirmer runs the value through the shared helper', () => {
  const surfaces = [
    '../app/hospitals/[id]/essence/page.tsx',
    '../app/hospitals/[id]/onboarding/page.tsx',
    '../app/hospitals/[id]/wiki/page.tsx',
    '../app/hospitals/[id]/dashboard/page.tsx',
  ]

  for (const surface of surfaces) {
    const source = readFileSync(new URL(surface, import.meta.url), 'utf8')
    assert.match(source, /from '@\/lib\/actor-display'/, surface)
  }
})
