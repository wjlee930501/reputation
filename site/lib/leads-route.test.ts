import assert from 'node:assert/strict'
import test from 'node:test'

import { POST } from './leads-route.ts'

const VALID_FIELDS = {
  clinicName: 'OOO정형외과',
  clinicAddress: '서울 강남구 테헤란로 00',
  directorName: '김원장',
  directorPhone: '010-1234-5678',
  homepage: 'https://clinic.example.com',
  specialty: '정형외과',
  regionKeyword: '강남역',
  coreKeywords: '도수치료, 허리통증, 도수치료',
}

function inquiryRequest(overrides: Partial<typeof VALID_FIELDS> = {}): Request {
  const formData = new FormData()
  const fields = { ...VALID_FIELDS, ...overrides }
  for (const [name, value] of Object.entries(fields)) formData.set(name, value)
  formData.set('privacy', 'on')
  formData.set('consent_version', 'v1.2026-08')
  formData.set('source_path', '/contact')
  formData.set('website', '')

  return new Request('https://site.example.com/api/leads', {
    method: 'POST',
    headers: { Accept: 'application/json' },
    body: formData,
  })
}

test('leads route requires every one of the eight inquiry fields', async () => {
  const originalFetch = globalThis.fetch
  let upstreamCalled = false
  globalThis.fetch = (async () => {
    upstreamCalled = true
    throw new Error('upstream must not be called')
  }) as typeof fetch

  try {
    for (const field of Object.keys(VALID_FIELDS) as (keyof typeof VALID_FIELDS)[]) {
      const response = await POST(inquiryRequest({ [field]: '' }))
      const body = await response.json() as { missingFields: string[] }

      assert.equal(response.status, 400)
      assert.deepEqual(body.missingFields, [field])
    }
    assert.equal(upstreamCalled, false)
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('leads route rejects malformed phone and homepage values before proxying', async () => {
  const originalFetch = globalThis.fetch
  let upstreamCalled = false
  globalThis.fetch = (async () => {
    upstreamCalled = true
    throw new Error('upstream must not be called')
  }) as typeof fetch

  try {
    const phoneResponse = await POST(inquiryRequest({ directorPhone: '02-1234-5678' }))
    assert.equal(phoneResponse.status, 400)
    assert.deepEqual((await phoneResponse.json()).invalidFields, ['directorPhone'])

    const homepageResponse = await POST(inquiryRequest({ homepage: 'clinic.example.com' }))
    assert.equal(homepageResponse.status, 400)
    assert.deepEqual((await homepageResponse.json()).invalidFields, ['homepage'])
    assert.equal(upstreamCalled, false)
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('leads route rejects diagnosis inputs that would leak the clinic name into the queries', async () => {
  const originalFetch = globalThis.fetch
  let upstreamCalled = false
  globalThis.fetch = (async () => {
    upstreamCalled = true
    throw new Error('upstream must not be called')
  }) as typeof fetch

  try {
    const response = await POST(inquiryRequest({ coreKeywords: 'OOO정형외과 도수치료' }))
    assert.equal(response.status, 400)
    const body = await response.json()
    assert.deepEqual(body.invalidFields, ['coreKeywords'])
    assert.match(body.error, /병원명/)

    const empty = await POST(inquiryRequest({ coreKeywords: ' , ' }))
    assert.equal(empty.status, 400)
    assert.equal(upstreamCalled, false)
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('leads route proxies a structured legacy payload without free-form fields', async () => {
  const originalFetch = globalThis.fetch
  let outboundUrl = ''
  let outboundPayload: unknown
  globalThis.fetch = (async (input, init) => {
    outboundUrl = String(input)
    outboundPayload = JSON.parse(String(init?.body))
    return Response.json({ ok: true, id: 'lead-1' })
  }) as typeof fetch

  try {
    const response = await POST(inquiryRequest())

    assert.equal(response.status, 200)
    assert.equal(outboundUrl, 'http://localhost:8000/api/v1/public/leads')
    assert.deepEqual(outboundPayload, {
      clinic_name: 'OOO정형외과',
      clinic_type: '도입문의',
      contact: '010-1234-5678',
      contact_name: '김원장',
      specialty: '정형외과',
      region_keyword: '강남역',
      core_keywords: ['도수치료', '허리통증'],
      question: [
        '병원 주소: 서울 강남구 테헤란로 00',
        '원장님 성함: 김원장',
        '병원 홈페이지: https://clinic.example.com',
      ].join('\n'),
      privacy: true,
      consent_version: 'v1.2026-08',
      source_path: '/contact',
    })
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('leads route passes the backend acknowledgement and diagnosis outcome through to the browser', async () => {
  const originalFetch = globalThis.fetch
  globalThis.fetch = (async () =>
    Response.json({ ok: true, lead_id: 'lead-1', created_at: null, diagnosis_id: 'diag-1', ack_sms: 'sent' })) as typeof fetch

  try {
    const response = await POST(inquiryRequest())
    assert.equal(response.status, 200)
    assert.deepEqual(await response.json(), {
      ok: true,
      lead_id: 'lead-1',
      created_at: null,
      diagnosis_id: 'diag-1',
      ack_sms: 'sent',
    })
  } finally {
    globalThis.fetch = originalFetch
  }
})
