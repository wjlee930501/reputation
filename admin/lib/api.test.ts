import assert from 'node:assert/strict'
import test from 'node:test'

import { fetchAPI } from './api.ts'

test('fetchAPI surfaces FastAPI detail messages instead of raw JSON', async () => {
  const originalFetch = globalThis.fetch
  globalThis.fetch = (async () =>
    new Response(JSON.stringify({ detail: '발행 요일을 추가해 주세요.' }), { status: 400 })) as typeof fetch

  try {
    await assert.rejects(
      () => fetchAPI('/admin/hospitals/demo/schedule', { method: 'POST' }),
      /발행 요일을 추가해 주세요\./,
    )
  } finally {
    globalThis.fetch = originalFetch
  }
})
