import assert from 'node:assert/strict'
import test from 'node:test'

import { resolveAssetUrl } from './api.ts'

test('resolveAssetUrl returns absolute http URLs unchanged', () => {
  assert.equal(resolveAssetUrl('https://cdn.example.com/image.png'), 'https://cdn.example.com/image.png')
  assert.equal(resolveAssetUrl('http://localhost:8000/assets/demo.png'), 'http://localhost:8000/assets/demo.png')
})

test('resolveAssetUrl resolves public API paths against the backend base', () => {
  assert.equal(
    resolveAssetUrl('/api/v1/public/hospitals/demo/assets/asset-id'),
    'http://localhost:8000/api/v1/public/hospitals/demo/assets/asset-id',
  )
})

test('resolveAssetUrl rejects internal storage paths and unsupported relative keys', () => {
  assert.equal(resolveAssetUrl('gs://bucket/private-image.png'), null)
  assert.equal(resolveAssetUrl('assets/private-image.png'), null)
  assert.equal(resolveAssetUrl('javascript:alert(1)'), null)
  assert.equal(resolveAssetUrl('   '), null)
})
