import assert from 'node:assert/strict'
import test from 'node:test'

import { PLAN_CONTRACT_LABELS, PLAN_LABELS } from '../types/index.ts'

test('plan catalog matches the current monthly volume and VAT-exclusive pricing', () => {
  assert.deepEqual(PLAN_LABELS, {
    PLAN_12: '스타터 · 월 12편',
    PLAN_16: '그로워 · 월 16편',
    PLAN_20: '리더 · 월 20편',
  })
  assert.deepEqual(PLAN_CONTRACT_LABELS, {
    PLAN_12: '스타터 · 월 12편 · 60만원 (부가세 별도)',
    PLAN_16: '그로워 · 월 16편 · 90만원 (부가세 별도)',
    PLAN_20: '리더 · 월 20편 · 120만원 (부가세 별도)',
  })
})
