import assert from 'node:assert/strict'
import test from 'node:test'

import { buildPostalAddress } from './clinic-schema.ts'

test('buildPostalAddress preserves the verified physical address without target regions', () => {
  assert.deepEqual(buildPostalAddress(' 서울특별시 강남구 테헤란로 1 '), {
    '@type': 'PostalAddress',
    streetAddress: '서울특별시 강남구 테헤란로 1',
    addressCountry: 'KR',
  })
})

test('buildPostalAddress omits an unknown physical address', () => {
  assert.equal(buildPostalAddress('  '), undefined)
  assert.equal(buildPostalAddress(null), undefined)
})
