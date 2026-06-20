import assert from 'node:assert/strict'
import test from 'node:test'

import { ContentNotFoundError, fetchContent, fetchContents, fetchHospital, resolveAssetUrl } from './api.ts'

function setEnv(name: 'NODE_ENV' | 'GCP_STORAGE_BUCKET', value: string | undefined): void {
  if (value === undefined) {
    Reflect.deleteProperty(process.env, name)
    return
  }
  Object.defineProperty(process.env, name, {
    configurable: true,
    enumerable: true,
    value,
    writable: true,
  })
}

test('resolveAssetUrl returns absolute http URLs unchanged', () => {
  assert.equal(resolveAssetUrl('http://localhost:8000/assets/demo.png'), 'http://localhost:8000/assets/demo.png')
})

test('resolveAssetUrl rejects localhost absolute asset URLs in production', () => {
  const originalNodeEnv = process.env.NODE_ENV
  try {
    setEnv('NODE_ENV', 'production')
    assert.equal(resolveAssetUrl('http://localhost:8000/assets/demo.png'), null)
    assert.equal(resolveAssetUrl('https://127.0.0.1/assets/demo.png'), null)
  } finally {
    setEnv('NODE_ENV', originalNodeEnv)
  }
})

test('resolveAssetUrl allows only the configured GCS asset bucket', () => {
  const originalBucket = process.env.GCP_STORAGE_BUCKET
  setEnv('GCP_STORAGE_BUCKET', 'reputation-images')
  try {
    assert.equal(
      resolveAssetUrl('https://storage.googleapis.com/reputation-images/content/demo.png?X-Goog-Signature=sig'),
      'https://storage.googleapis.com/reputation-images/content/demo.png?X-Goog-Signature=sig',
    )
    assert.equal(
      resolveAssetUrl('https://reputation-images.storage.googleapis.com/content/demo.png?X-Goog-Signature=sig'),
      'https://reputation-images.storage.googleapis.com/content/demo.png?X-Goog-Signature=sig',
    )
    assert.equal(resolveAssetUrl('https://storage.googleapis.com/attacker-bucket/pixel.png'), null)
    assert.equal(resolveAssetUrl('https://cdn.storage.googleapis.com/attacker-bucket/pixel.png'), null)
  } finally {
    setEnv('GCP_STORAGE_BUCKET', originalBucket)
  }
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
  assert.equal(resolveAssetUrl('https://tracker.example.com/pixel.png'), null)
  assert.equal(resolveAssetUrl('https://cdn.example.com/image.png'), null)
  assert.equal(resolveAssetUrl('   '), null)
})

test('fetchHospital rejects malformed backend payloads at runtime', async () => {
  const originalFetch = globalThis.fetch
  globalThis.fetch = (async () =>
    new Response(JSON.stringify({ id: 123, slug: null, name: 'broken' }), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    })) as typeof fetch
  try {
    await assert.rejects(
      () => fetchHospital('demo-clinic'),
      /Invalid hospital payload/,
    )
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('fetchHospital normalizes unsafe hospital URL fields before rendering', async () => {
  const originalFetch = globalThis.fetch
  globalThis.fetch = (async () =>
    new Response(
      JSON.stringify(hospitalPayload({
        website_url: 'javascript:alert(1)',
        blog_url: 'https://blog.example.test/path',
        google_maps_url: 'javascript:alert(2)',
        naver_place_url: 'http://map.example.test/place',
      })),
      {
        status: 200,
        headers: { 'content-type': 'application/json' },
      },
    )) as typeof fetch
  try {
    const hospital = await fetchHospital('demo-clinic')
    assert.equal(hospital.website_url, null)
    assert.equal(hospital.blog_url, 'https://blog.example.test/path')
    assert.equal(hospital.google_maps_url, null)
    assert.equal(hospital.naver_place_url, 'http://map.example.test/place')
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('fetchHospital accepts backend-nullable hospital fields and normalizes UI defaults', async () => {
  const originalFetch = globalThis.fetch
  globalThis.fetch = (async () =>
    new Response(
      JSON.stringify(hospitalPayload({
        address: null,
        phone: null,
        business_hours: null,
        director_name: null,
        director_career: null,
      })),
      {
        status: 200,
        headers: { 'content-type': 'application/json' },
      },
    )) as typeof fetch
  try {
    const hospital = await fetchHospital('demo-clinic')
    assert.equal(hospital.address, '')
    assert.equal(hospital.phone, '')
    assert.deepEqual(hospital.business_hours, {})
    assert.equal(hospital.director_name, '')
    assert.equal(hospital.director_career, '')
  } finally {
    globalThis.fetch = originalFetch
  }
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
    new Response(JSON.stringify(contentPayload({ id: 'abc', title: '제목', body: '본문' })), {
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

test('fetchContents rejects malformed backend payloads at runtime', async () => {
  const originalFetch = globalThis.fetch
  globalThis.fetch = (async () =>
    new Response(JSON.stringify([{ id: 'abc', title: 42 }]), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    })) as typeof fetch
  try {
    await assert.rejects(() => fetchContents('demo-clinic'), /Invalid contents payload/)
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('fetchContent rejects malformed backend payloads at runtime', async () => {
  const originalFetch = globalThis.fetch
  globalThis.fetch = (async () =>
    new Response(JSON.stringify(contentPayload({ body: null })), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    })) as typeof fetch
  try {
    await assert.rejects(() => fetchContent('demo-clinic', 'abc'), /Invalid content payload/)
  } finally {
    globalThis.fetch = originalFetch
  }
})

function contentPayload(overrides: Record<string, unknown> = {}) {
  return {
    id: 'abc',
    content_type: 'FAQ',
    title: '제목',
    meta_description: null,
    image_url: null,
    scheduled_date: '2026-06-20',
    published_at: null,
    body_updated_at: null,
    references: [],
    faq_question: null,
    faq_answer_summary: null,
    body: '본문',
    ...overrides,
  }
}

function hospitalPayload(overrides: Record<string, unknown> = {}) {
  return {
    id: 'hospital-id',
    name: '데모의원',
    slug: 'demo-clinic',
    address: '서울시 강남구',
    phone: '02-0000-0000',
    business_hours: { mon: '09:00-18:00' },
    website_url: null,
    blog_url: null,
    kakao_channel_url: null,
    google_business_profile_url: null,
    google_maps_url: null,
    naver_place_url: null,
    latitude: null,
    longitude: null,
    wikidata_qid: null,
    gbp_place_id: null,
    naver_place_id: null,
    kakao_place_id: null,
    hira_org_id: null,
    region: ['강남'],
    specialties: ['정형외과'],
    keywords: ['어깨'],
    director_name: '홍길동',
    director_career: '전문의',
    director_philosophy: null,
    director_photo_url: null,
    director_credentials: null,
    treatments: [{ name: '도수치료', description: '설명' }],
    aeo_domain: null,
    photos: [],
    ...overrides,
  }
}
