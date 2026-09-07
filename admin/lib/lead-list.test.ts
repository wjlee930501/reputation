import assert from 'node:assert/strict'
import test from 'node:test'

import { leadEmptyState } from './lead-list.ts'

test('filtered-empty copy states the real total and differs from data-empty copy', () => {
  assert.deepEqual(leadEmptyState(true, 8), {
    title: '조건에 맞는 상담 요청이 없습니다 (전체 8건)',
    detail: '확인 필요는 신규 요청이거나 첫 연락 기한을 넘긴 미연락 상담 요청입니다.',
  })
  assert.equal(leadEmptyState(false, 0).title, '아직 접수된 상담 요청이 없습니다.')
})
