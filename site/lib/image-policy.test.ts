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
  assert.equal(shouldBypassNextImageOptimization(null), false)
})
