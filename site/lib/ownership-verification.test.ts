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

test('per-hospital Google and Bing tokens join the platform Google token', () => {
  assert.deepEqual(
    ownershipVerificationMetadata('naver-token-123', 'platform-google', 'clinic-google-123', 'BING0123456789AB'),
    {
      google: ['platform-google', 'clinic-google-123'],
      other: { 'naver-site-verification': 'naver-token-123', 'msvalidate.01': 'BING0123456789AB' },
    },
  )
  assert.deepEqual(ownershipVerificationMetadata(null, null, 'clinic-google-123', null), {
    google: 'clinic-google-123',
  })
  // 같은 토큰은 한 번만, 형식이 깨진 병원 토큰은 싣지 않는다.
  assert.deepEqual(ownershipVerificationMetadata(null, 'same-token-123', 'same-token-123', 'bad token'), {
    google: 'same-token-123',
  })
  assert.equal(ownershipVerificationMetadata(null, null, '<script>', 'x'), undefined)
})
