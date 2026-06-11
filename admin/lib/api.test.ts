import assert from 'node:assert/strict'
import test from 'node:test'

import { ApiError, fetchAPI } from './api.ts'

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

test('fetchAPI converts FastAPI validation arrays into readable lines', async () => {
  const originalFetch = globalThis.fetch
  globalThis.fetch = (async () =>
    new Response(
      JSON.stringify({
        detail: [
          { loc: ['body', 'publish_days'], msg: 'publish_days must not be empty', type: 'value_error' },
          { loc: ['body', 'plan'], msg: 'invalid plan', type: 'value_error' },
        ],
      }),
      { status: 422 },
    )) as typeof fetch

  try {
    await assert.rejects(
      () => fetchAPI('/admin/hospitals/demo/schedule', { method: 'POST' }),
      (err: unknown) => {
        assert.ok(err instanceof ApiError)
        assert.equal(err.status, 422)
        assert.match(err.message, /publish_days: publish_days must not be empty/)
        assert.match(err.message, /plan: invalid plan/)
        assert.doesNotMatch(err.message, /\{/)
        return true
      },
    )
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('fetchAPI converts grounding_errors detail into readable Korean message', async () => {
  const originalFetch = globalThis.fetch
  globalThis.fetch = (async () =>
    new Response(
      JSON.stringify({ detail: { grounding_errors: ['근거 노트 없는 주장: 수술 1만 건'] } }),
      { status: 422 },
    )) as typeof fetch

  try {
    await assert.rejects(
      () => fetchAPI('/admin/hospitals/demo/essence/philosophy/draft', { method: 'POST' }),
      (err: unknown) => {
        assert.ok(err instanceof ApiError)
        assert.match(err.message, /근거 검증에 실패한 항목이 있습니다\./)
        assert.match(err.message, /근거 노트 없는 주장: 수술 1만 건/)
        return true
      },
    )
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('fetchAPI keeps structured detail on ApiError for screens that need violations', async () => {
  const originalFetch = globalThis.fetch
  globalThis.fetch = (async () =>
    new Response(
      JSON.stringify({ detail: { message: '의료광고 금지 표현이 포함되어 있습니다.', violations: ['최고', '완치'] } }),
      { status: 400 },
    )) as typeof fetch

  try {
    await assert.rejects(
      () => fetchAPI('/admin/hospitals/demo/content/x/publish', { method: 'POST' }),
      (err: unknown) => {
        assert.ok(err instanceof ApiError)
        assert.match(err.message, /의료광고 금지 표현이 포함되어 있습니다\. \(최고, 완치\)/)
        const detail = err.detail as { violations?: string[] }
        assert.deepEqual(detail.violations, ['최고', '완치'])
        return true
      },
    )
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('fetchAPI appends compact JSON for unknown structured detail (cause stays visible)', async () => {
  const originalFetch = globalThis.fetch
  globalThis.fetch = (async () =>
    new Response(JSON.stringify({ detail: { some_internal: { nested: 1 } } }), { status: 400 })) as typeof fetch

  try {
    await assert.rejects(
      () => fetchAPI('/admin/hospitals/demo', { method: 'POST' }),
      (err: unknown) => {
        assert.ok(err instanceof ApiError)
        assert.match(err.message, /^입력값이 올바르지 않습니다\./)
        assert.match(err.message, /"some_internal":\{"nested":1\}/)
        return true
      },
    )
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('fetchAPI truncates very long unknown detail JSON to ~200 chars', async () => {
  const originalFetch = globalThis.fetch
  globalThis.fetch = (async () =>
    new Response(JSON.stringify({ detail: { dump: 'x'.repeat(500) } }), { status: 500 })) as typeof fetch

  try {
    await assert.rejects(
      () => fetchAPI('/admin/hospitals/demo', { method: 'POST' }),
      (err: unknown) => {
        assert.ok(err instanceof ApiError)
        assert.match(err.message, /^서버 오류가 발생했습니다\./)
        assert.ok(err.message.length < 300)
        assert.match(err.message, /…/)
        return true
      },
    )
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('fetchAPI surfaces top-level error/message strings when detail is absent', async () => {
  const originalFetch = globalThis.fetch
  globalThis.fetch = (async () =>
    new Response(JSON.stringify({ error: 'Server misconfigured' }), { status: 500 })) as typeof fetch

  try {
    await assert.rejects(
      () => fetchAPI('/admin/hospitals/demo', { method: 'POST' }),
      (err: unknown) => {
        assert.ok(err instanceof ApiError)
        assert.equal(err.message, 'Server misconfigured')
        return true
      },
    )
  } finally {
    globalThis.fetch = originalFetch
  }

  globalThis.fetch = (async () =>
    new Response(JSON.stringify({ message: '점검 중입니다' }), { status: 503 })) as typeof fetch
  try {
    await assert.rejects(
      () => fetchAPI('/admin/hospitals/demo', { method: 'POST' }),
      (err: unknown) => {
        assert.ok(err instanceof ApiError)
        assert.equal(err.message, '점검 중입니다')
        return true
      },
    )
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('fetchAPI sends FormData bodies without forcing a JSON content type', async () => {
  const originalFetch = globalThis.fetch
  let capturedHeaders: Record<string, string> | undefined
  let capturedBody: unknown
  globalThis.fetch = (async (_input: unknown, init?: RequestInit) => {
    capturedHeaders = init?.headers as Record<string, string>
    capturedBody = init?.body
    return new Response(JSON.stringify({ ok: true }), { status: 200 })
  }) as typeof fetch

  try {
    const fd = new FormData()
    fd.append('title', '원장 사진')
    const result = await fetchAPI<{ ok: boolean }>('/admin/hospitals/demo/essence/sources/upload', {
      method: 'POST',
      body: fd,
    })
    assert.equal(result.ok, true)
    assert.ok(capturedBody instanceof FormData)
    assert.equal(capturedHeaders?.['Content-Type'], undefined)
  } finally {
    globalThis.fetch = originalFetch
  }
})
