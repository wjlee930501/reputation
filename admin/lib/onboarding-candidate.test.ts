import assert from 'node:assert/strict'
import test from 'node:test'

import {
  addProfileUrlCandidate,
  type ProfileUrlCandidate,
} from './onboarding-candidate.ts'

function candidate(key: string, sourceType = 'OTHER'): ProfileUrlCandidate {
  return {
    key,
    title: `candidate ${key}`,
    sourceType,
    url: `https://example.test/${key}`,
  }
}

test('homepage and blog candidates use the crawl endpoint', async () => {
  const calls: Array<{ path: string; body: Record<string, string> }> = []
  const fetcher = async (path: string, options: { method: 'POST'; body: string }) => {
    calls.push({ path, body: JSON.parse(options.body) as Record<string, string> })
    return null
  }

  assert.equal(
    await addProfileUrlCandidate(fetcher, 'hospital-id', candidate('website_url', 'HOMEPAGE')),
    'crawled',
  )
  assert.equal(
    await addProfileUrlCandidate(fetcher, 'hospital-id', candidate('blog_url', 'NAVER_BLOG')),
    'crawled',
  )
  assert.deepEqual(
    calls.map((call) => call.path),
    [
      '/admin/hospitals/hospital-id/essence/sources/crawl',
      '/admin/hospitals/hospital-id/essence/sources/crawl',
    ],
  )
  assert.deepEqual(calls.map((call) => call.body.source_type), ['HOMEPAGE', 'NAVER_BLOG'])
})

test('place and profile channel candidates never create a source row', async () => {
  let requestCount = 0
  const fetcher = async () => {
    requestCount += 1
    return null
  }

  for (const key of [
    'naver_place_url',
    'google_business_profile_url',
    'google_maps_url',
    'kakao_channel_url',
  ]) {
    assert.equal(
      await addProfileUrlCandidate(fetcher, 'hospital-id', candidate(key)),
      'already_in_profile',
    )
  }
  assert.equal(requestCount, 0)
})

test('a crawl 4xx is surfaced without a plain-source fallback row', async () => {
  const paths: string[] = []
  const responseError = Object.assign(new Error('invalid URL'), { status: 422 })
  const fetcher = async (path: string) => {
    paths.push(path)
    throw responseError
  }

  await assert.rejects(
    addProfileUrlCandidate(fetcher, 'hospital-id', candidate('website_url', 'HOMEPAGE')),
    responseError,
  )
  assert.deepEqual(paths, ['/admin/hospitals/hospital-id/essence/sources/crawl'])
  assert.equal(paths.some((path) => path.endsWith('/essence/sources')), false)
})
