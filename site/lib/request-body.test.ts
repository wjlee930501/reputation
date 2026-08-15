import assert from 'node:assert/strict'
import test from 'node:test'

import { BodyTooLargeError, readFormDataBodyWithLimit, readJsonBodyWithLimit } from './request-body.ts'

test('JSON body cap rejects oversized requests without Content-Length', async () => {
  const request = new Request('https://site.test/api/diagnosis', {
    method: 'POST',
    body: JSON.stringify({ clinic_name: 'x'.repeat(1024) }),
    headers: { 'Content-Type': 'application/json' },
  })
  assert.equal(request.headers.get('content-length'), null)
  await assert.rejects(
    () => readJsonBodyWithLimit(request, 128),
    (error) => error instanceof BodyTooLargeError && error.maxBytes === 128,
  )
})

test('JSON body cap counts bytes even when Content-Length is falsely small', async () => {
  const request = new Request('https://site.test/api/diagnosis', {
    method: 'POST',
    body: JSON.stringify({ clinic_name: 'x'.repeat(1024) }),
    headers: { 'Content-Type': 'application/json', 'Content-Length': '1' },
  })
  await assert.rejects(
    () => readJsonBodyWithLimit(request, 128),
    (error) => error instanceof BodyTooLargeError && error.maxBytes === 128,
  )
})

test('JSON body cap allows normal JSON requests', async () => {
  const request = new Request('https://site.test/api/diagnosis', {
    method: 'POST',
    body: JSON.stringify({ clinic_name: '장편한외과의원', core_keywords: ['치질'] }),
    headers: { 'Content-Type': 'application/json' },
  })
  assert.deepEqual(await readJsonBodyWithLimit(request, 4096), {
    clinic_name: '장편한외과의원',
    core_keywords: ['치질'],
  })
})

test('multipart form cap rejects oversized requests without Content-Length', async () => {
  const boundary = 'test-boundary'
  const multipartBody = [
    `--${boundary}`,
    'Content-Disposition: form-data; name="clinicName"',
    '',
    'x'.repeat(2048),
    `--${boundary}--`,
    '',
  ].join('\r\n')
  const request = new Request('https://site.test/api/leads', {
    method: 'POST',
    body: multipartBody,
    headers: { 'Content-Type': `multipart/form-data; boundary=${boundary}` },
  })
  assert.equal(request.headers.get('content-length'), null)
  await assert.rejects(
    () => readFormDataBodyWithLimit(request, 256),
    (error) => error instanceof BodyTooLargeError && error.maxBytes === 256,
  )
})

test('multipart form cap preserves the Content-Type boundary', async () => {
  const formData = new FormData()
  formData.set('clinicName', '장편한외과의원')
  formData.set('privacy', 'on')
  const request = new Request('https://site.test/api/leads', { method: 'POST', body: formData })
  const parsed = await readFormDataBodyWithLimit(request, 4096)
  assert.equal(parsed.get('clinicName'), '장편한외과의원')
  assert.equal(parsed.get('privacy'), 'on')
})
