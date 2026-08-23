import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  EXPOSURE_ACTION_LIST_LIMIT,
  describeExposureActions,
  summarizeExposureActions,
} from './exposure-action-counts.ts'

const sample = [
  { status: 'OPEN' },
  { status: 'OPEN' },
  { status: 'IN_PROGRESS' },
  { status: 'BLOCKED' },
]

test('waiting counts only OPEN, so the two screens can no longer disagree', () => {
  const summary = summarizeExposureActions(sample)

  assert.equal(summary.waiting, 2)
  assert.equal(summary.inProgress, 1)
  assert.equal(summary.blocked, 1)
  assert.equal(summary.active, 4)
})

test('a list filled to the limit admits the number is a floor', () => {
  const full = Array.from({ length: EXPOSURE_ACTION_LIST_LIMIT }, () => ({ status: 'OPEN' }))
  const summary = summarizeExposureActions(full)

  assert.equal(summary.truncated, true)
  assert.match(describeExposureActions(summary), /상위 20건 기준/)
})

test('a short list never claims to be truncated', () => {
  const summary = summarizeExposureActions(sample)

  assert.equal(summary.truncated, false)
  assert.equal(describeExposureActions(summary), '대기 2건 · 진행중 1건 · 확인필요 1건')
})

test('the summary hides the blocked clause when nothing is blocked', () => {
  const summary = summarizeExposureActions([{ status: 'OPEN' }, { status: 'IN_PROGRESS' }])

  assert.equal(describeExposureActions(summary), '대기 1건 · 진행중 1건')
})

test('an empty queue says so rather than reporting zeroes', () => {
  assert.equal(describeExposureActions(summarizeExposureActions([])), '진단된 보완 작업이 없습니다')
})

test('both screens read the same limit and the same counter', () => {
  const dashboard = readFileSync(
    new URL('../app/hospitals/[id]/dashboard/page.tsx', import.meta.url),
    'utf8',
  )
  const queue = readFileSync(
    new URL('../app/hospitals/[id]/exposure-actions/page.tsx', import.meta.url),
    'utf8',
  )

  for (const source of [dashboard, queue]) {
    assert.match(source, /summarizeExposureActions/)
    assert.match(source, /EXPOSURE_ACTION_LIST_LIMIT/)
  }
  // 대시보드가 다시 5건만 받아 세면 두 화면이 또 갈라진다.
  assert.doesNotMatch(dashboard, /exposure-actions\?limit=5/)
})

test('the completed status is never counted from a list that cannot contain it', () => {
  // 백엔드 list_top_exposure_actions는 OPEN/IN_PROGRESS/BLOCKED만 돌려준다.
  const summary = summarizeExposureActions([{ status: 'COMPLETED' }])

  assert.equal(summary.active, 0)
  assert.equal(Object.keys(summary).includes('completed'), false)

  const queue = readFileSync(
    new URL('../app/hospitals/[id]/exposure-actions/page.tsx', import.meta.url),
    'utf8',
  )
  assert.doesNotMatch(queue, /label="완료"/)
})
