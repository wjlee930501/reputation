import assert from 'node:assert/strict'
import test from 'node:test'

import { constantTimeEqual } from './constant-time.ts'

test('constantTimeEqual returns true for identical secrets', () => {
  assert.equal(constantTimeEqual('s3cr3t-revalidate-token', 's3cr3t-revalidate-token'), true)
})

test('constantTimeEqual returns false for a wrong secret of equal length', () => {
  assert.equal(constantTimeEqual('s3cr3t-revalidate-tokeX', 's3cr3t-revalidate-token'), false)
})

test('constantTimeEqual returns false for a wrong secret of different length', () => {
  // 길이가 달라도 예외 없이 false (SHA-256 다이제스트는 항상 동일 길이)
  assert.equal(constantTimeEqual('short', 's3cr3t-revalidate-token'), false)
  assert.equal(constantTimeEqual('', 's3cr3t-revalidate-token'), false)
})
