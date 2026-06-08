import assert from 'node:assert/strict'
import test from 'node:test'

import { shouldBypassNextImageOptimization } from './image-policy.ts'

test('shouldBypassNextImageOptimization returns true for public API asset URLs', () => {
  assert.equal(
    shouldBypassNextImageOptimization(
      'https://api.example.com/api/v1/public/hospitals/test-hospital/assets/asset-id',
    ),
    true,
  )
  assert.equal(
    shouldBypassNextImageOptimization(
      'http://127.0.0.1:18081/api/v1/public/hospitals/test-hospital/assets/asset-id',
    ),
    true,
  )
})

test('shouldBypassNextImageOptimization returns true for backend asset URLs', () => {
  assert.equal(
    shouldBypassNextImageOptimization(
      'http://localhost:8000/assets/hospital-id/doctor-demo.png',
    ),
    true,
  )
  assert.equal(
    shouldBypassNextImageOptimization('/assets/hospital-id/clinic-demo.png'),
    true,
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
