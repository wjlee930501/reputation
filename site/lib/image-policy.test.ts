import assert from 'node:assert/strict'
import test from 'node:test'

import { clinicLogoPresentation, shouldBypassNextImageOptimization } from './image-policy.ts'

test('public API assets stay on the responsive image optimizer path', () => {
  assert.equal(
    shouldBypassNextImageOptimization(
      'https://reputation.motionlabs.kr/api/v1/public/hospitals/test-hospital/assets/asset-id',
    ),
    false,
  )
  assert.equal(
    shouldBypassNextImageOptimization('/assets/hospital-id/clinic-demo.png'),
    false,
  )
})

test('shouldBypassNextImageOptimization keeps normal optimized image hosts untouched', () => {
  assert.equal(shouldBypassNextImageOptimization('https://storage.googleapis.com/bucket/image.png'), false)
  assert.equal(shouldBypassNextImageOptimization('https://cdn.storage.googleapis.com/bucket/image.png'), false)
  assert.equal(shouldBypassNextImageOptimization(null), false)
})

test('off-allowlist external hosts are identified for explicit typographic fallbacks', () => {
  // Given arbitrary external URLs that cannot use the controlled optimizer path
  // When their host eligibility is checked
  // Then callers can reject them to a visible non-image fallback instead of serving originals.
  assert.equal(shouldBypassNextImageOptimization('https://phinf.pstatic.net/clinic/director.jpg'), true)
  assert.equal(shouldBypassNextImageOptimization('https://example.com/photo.png'), true)
  assert.equal(shouldBypassNextImageOptimization('http://some-clinic-cdn.kr/doctor.jpg'), true)
})

test('an arbitrary logo URL resolves to the typographic fallback before image delivery', () => {
  // Given an AE-pasted logo URL outside the controlled image origins
  // When the header resolves its presentation state
  // Then it uses the hospital-name fallback instead of attempting the original upload.
  assert.deepEqual(clinicLogoPresentation('https://example.com/logo.png'), { kind: 'fallback' })
  assert.deepEqual(clinicLogoPresentation('https://storage.googleapis.com/reputation-images/logo.png'), {
    kind: 'image',
    src: 'https://storage.googleapis.com/reputation-images/logo.png',
  })
})
