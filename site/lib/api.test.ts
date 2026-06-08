import assert from 'node:assert/strict'
import test from 'node:test'

import { ContentNotFoundError, fetchContent, resolveAssetUrl } from './api.ts'

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

test('fetchContent throws ContentNotFoundError on a 404 (so the page can call notFound())', async () => {
  const originalFetch = globalThis.fetch
  globalThis.fetch = (async () => new Response('not found', { status: 404 })) as typeof fetch
  try {
    await assert.rejects(
      () => fetchContent('demo-clinic', 'missing-content-id'),
      (err) => err instanceof ContentNotFoundError,
    )
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('fetchContent throws a generic error on other non-ok statuses (surfaces as 500)', async () => {
  const originalFetch = globalThis.fetch
  globalThis.fetch = (async () => new Response('boom', { status: 503 })) as typeof fetch
  try {
    await assert.rejects(
      () => fetchContent('demo-clinic', 'some-id'),
      (err) => err instanceof Error && !(err instanceof ContentNotFoundError),
    )
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('fetchContent returns parsed JSON on a 200', async () => {
  const originalFetch = globalThis.fetch
  globalThis.fetch = (async () =>
    new Response(JSON.stringify({ id: 'abc', title: '제목' }), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    })) as typeof fetch
  try {
    const content = await fetchContent('demo-clinic', 'abc')
    assert.equal(content.id, 'abc')
    assert.equal(content.title, '제목')
  } finally {
    globalThis.fetch = originalFetch
  }
})
