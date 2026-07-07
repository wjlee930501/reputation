import assert from 'node:assert/strict'
import test from 'node:test'

import { buildAddressRegionFields } from './clinic-schema.ts'

test('buildAddressRegionFields maps region[0]→region, region[1]→locality', () => {
  assert.deepEqual(buildAddressRegionFields(['서울', '강남구']), {
    addressRegion: '서울',
    addressLocality: '강남구',
  })
})

test('buildAddressRegionFields omits missing parts and trims', () => {
  assert.deepEqual(buildAddressRegionFields(['  서울  ']), { addressRegion: '서울' })
  assert.deepEqual(buildAddressRegionFields([]), {})
  assert.deepEqual(buildAddressRegionFields(null), {})
  assert.deepEqual(buildAddressRegionFields(['', '강남구']), { addressRegion: '강남구' })
})
