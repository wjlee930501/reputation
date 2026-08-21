import assert from 'node:assert/strict'
import test from 'node:test'

import { shouldBypassNextImageOptimization } from './image-policy.ts'

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

test('shouldBypassNextImageOptimization bypasses off-allowlist external hosts (e.g. AE-pasted director photo)', () => {
  // next.config remotePatterns에 없는 외부 호스트는 next/image 최적화 시 400이 나므로 우회해야 한다.
  assert.equal(shouldBypassNextImageOptimization('https://phinf.pstatic.net/clinic/director.jpg'), true)
  assert.equal(shouldBypassNextImageOptimization('https://example.com/photo.png'), true)
  assert.equal(shouldBypassNextImageOptimization('http://some-clinic-cdn.kr/doctor.jpg'), true)
})
