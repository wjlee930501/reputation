import assert from 'node:assert/strict'
import test from 'node:test'

import { contentCoverImageSource, isOffAllowlistExternalUrl } from './image-policy.ts'

test('content covers render certified payload URLs or the existing null motif', () => {
  assert.equal(contentCoverImageSource('/api/v1/public/hospitals/a/contents/b/image?v=abc'), '/api/v1/public/hospitals/a/contents/b/image?v=abc')
  assert.equal(contentCoverImageSource(' https://storage.googleapis.com/bucket/image.png '), 'https://storage.googleapis.com/bucket/image.png')
  assert.equal(contentCoverImageSource(null), null)
  assert.equal(contentCoverImageSource('   '), null)
  assert.equal(contentCoverImageSource('javascript:alert(1)'), null)
  assert.equal(contentCoverImageSource('//attacker.example/image.png'), null)
  assert.equal(contentCoverImageSource('not a URL'), null)
})

test('public API assets stay on the responsive image optimizer path', () => {
  assert.equal(
    isOffAllowlistExternalUrl(
      'https://reputation.motionlabs.kr/api/v1/public/hospitals/test-hospital/assets/asset-id',
    ),
    false,
  )
  assert.equal(
    isOffAllowlistExternalUrl('/assets/hospital-id/clinic-demo.png'),
    false,
  )
})

test('isOffAllowlistExternalUrl keeps normal optimized image hosts untouched', () => {
  assert.equal(isOffAllowlistExternalUrl('https://storage.googleapis.com/bucket/image.png'), false)
  assert.equal(isOffAllowlistExternalUrl('https://cdn.storage.googleapis.com/bucket/image.png'), false)
  assert.equal(isOffAllowlistExternalUrl(null), false)
})

test('isOffAllowlistExternalUrl bypasses off-allowlist external hosts (e.g. AE-pasted director photo)', () => {
  // next.config remotePatterns에 없는 외부 호스트는 next/image 최적화 시 400이 나므로 우회해야 한다.
  assert.equal(isOffAllowlistExternalUrl('https://phinf.pstatic.net/clinic/director.jpg'), true)
  assert.equal(isOffAllowlistExternalUrl('https://example.com/photo.png'), true)
  assert.equal(isOffAllowlistExternalUrl('http://some-clinic-cdn.kr/doctor.jpg'), true)
})
