import assert from 'node:assert/strict'
import test from 'node:test'

import {
  ownershipVerificationMetadata,
  validDaumWebmasterVerification,
} from './ownership-verification.ts'

test('hospital metadata emits the per-hospital Naver token and preserves platform Google', () => {
  assert.deepEqual(
    ownershipVerificationMetadata('naver-token-123', 'google-token-456'),
    {
      google: 'google-token-456',
      other: { 'naver-site-verification': 'naver-token-123' },
    },
  )
})

test('invalid Naver values are omitted instead of becoming metadata', () => {
  assert.equal(ownershipVerificationMetadata('token with spaces', null), undefined)
  assert.deepEqual(ownershipVerificationMetadata('token with spaces', 'google-token'), {
    google: 'google-token',
  })
})

test('Daum verification accepts one official-shaped line only', () => {
  const phrase = '#DaumWebMasterTool:abc123:token=='
  assert.equal(validDaumWebmasterVerification(phrase), phrase)
  assert.equal(validDaumWebmasterVerification(`${phrase}\nDisallow: /`), null)
  assert.equal(validDaumWebmasterVerification('abc123:token=='), null)
})
