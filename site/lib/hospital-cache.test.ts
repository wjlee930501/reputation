import assert from 'node:assert/strict'
import test from 'node:test'
import { hospitalCacheTag, hospitalTagsForPaths, withHospitalCache } from './hospital-cache.ts'

test('one tenant invalidates all its data without touching platform or other clinics', () => {
  assert.deepEqual(hospitalTagsForPaths(['/', '/sitemap.xml', '/llms.txt', '/clinic-a', '/clinic-a/contents/123']), ['hospital:clinic-a'])
  assert.deepEqual(hospitalTagsForPaths(['/clinic-a', '/clinic-b']), ['hospital:clinic-a', 'hospital:clinic-b'])
})
test('fetch and invalidation tags match, including unicode slugs', () => {
  for (const slug of ['clinic-a', '의원']) {
    const init = withHospitalCache(slug, { next: { revalidate: 1800 } } as RequestInit) as RequestInit & { next: { revalidate: number; tags: string[] } }
    assert.equal(init.next.revalidate, 1800)
    assert.deepEqual(init.next.tags, hospitalTagsForPaths([`/${slug}/contents/id`]))
  }
})
test('no-store requests stay no-store and unsafe input cannot create shared tags', () => {
  const init = { cache: 'no-store' as const }
  assert.equal(withHospitalCache('clinic-a', init), init)
  assert.equal(hospitalCacheTag('a/b'), null)
  assert.equal(hospitalCacheTag('a'.repeat(201)), null)
  assert.deepEqual(hospitalTagsForPaths(['//clinic-a', '/api/revalidate']), [])
})
