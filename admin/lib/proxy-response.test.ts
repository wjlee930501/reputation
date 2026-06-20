import assert from 'node:assert/strict'
import test from 'node:test'

import { buildProxyResponse } from './proxy-response.ts'

test('buildProxyResponse preserves binary upstream bodies', async () => {
  const bytes = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x00, 0xff])
  const upstream = new Response(bytes, {
    status: 200,
    headers: {
      'content-type': 'image/png',
      'content-disposition': 'inline; filename="doctor.png"',
    },
  })

  const forwarded = buildProxyResponse(upstream)

  assert.equal(forwarded.status, 200)
  assert.equal(forwarded.headers.get('content-type'), 'image/png')
  assert.equal(forwarded.headers.get('content-disposition'), 'inline; filename="doctor.png"')
  assert.deepEqual(new Uint8Array(await forwarded.arrayBuffer()), bytes)
})

test('buildProxyResponse forces private no-store for admin responses', async () => {
  const upstream = new Response('secret', {
    status: 200,
    headers: {
      'cache-control': 'public, max-age=3600',
      etag: '"upstream-cache-token"',
    },
  })

  const forwarded = buildProxyResponse(upstream)

  assert.equal(forwarded.headers.get('cache-control'), 'no-store, private')
  assert.equal(forwarded.headers.get('etag'), null)
})
