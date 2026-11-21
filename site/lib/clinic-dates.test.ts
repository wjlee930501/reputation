import assert from 'node:assert/strict'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { dirname, join } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import { articleDateLabels, contentModifiedAt, formatSeoulDate, formatSeoulDotDate, seoulDateKey } from './clinic-dates.ts'

// 08:00 KST 발행은 전날 23:00Z다 — UTC로 그리면 하루 전 날짜가 된다(행복드림 11.02 글).
const PUBLISHED_0800_KST = '2026-11-01T23:00:15+00:00'

test('dates are drawn in Korea time regardless of the server time zone', () => {
  assert.equal(seoulDateKey(PUBLISHED_0800_KST), '2026-11-02')
  assert.equal(formatSeoulDate(PUBLISHED_0800_KST, ''), '2026년 11월 2일')
  assert.equal(formatSeoulDotDate(PUBLISHED_0800_KST), '2026.11.02')
  assert.equal(formatSeoulDate(null, '2026-11-20'), '2026-11-20')
  assert.equal(formatSeoulDate('not-a-date', 'x'), 'x')
})

test('an edit made before publication is not shown as a later update', () => {
  const labels = articleDateLabels({
    published_at: PUBLISHED_0800_KST,
    scheduled_date: '2026-11-02',
    body_updated_at: '2026-10-30T05:00:00+00:00',
  })
  assert.equal(labels.published.label, '2026년 11월 2일')
  assert.equal(labels.updated, null)
})

test('a same-day edit is not shown as an update either', () => {
  const labels = articleDateLabels({
    published_at: PUBLISHED_0800_KST,
    scheduled_date: '2026-11-02',
    body_updated_at: '2026-11-02T03:00:00+00:00',
  })
  assert.equal(labels.updated, null)
})

test('an edit after publication is shown with its Korea-time date', () => {
  const labels = articleDateLabels({
    published_at: PUBLISHED_0800_KST,
    scheduled_date: '2026-11-02',
    body_updated_at: '2026-11-10T16:30:00+00:00',
  })
  assert.deepEqual(labels.updated, { label: '2026년 11월 11일', value: '2026-11-10T16:30:00+00:00' })
})

test('an unpublished article falls back to its scheduled date', () => {
  const labels = articleDateLabels({ published_at: null, scheduled_date: '2026-11-20', body_updated_at: null })
  assert.equal(labels.published.label, '2026-11-20')
  assert.equal(labels.updated, null)
})

test('clinic pages do not format dates in the server time zone', () => {
  const root = join(dirname(fileURLToPath(import.meta.url)), '..', 'app', '[slug]')
  const offenders: string[] = []
  const walk = (dir: string) => {
    for (const name of readdirSync(dir)) {
      const path = join(dir, name)
      if (statSync(path).isDirectory()) walk(path)
      else if (/\.tsx?$/.test(name) && /toLocaleDateString\(/.test(readFileSync(path, 'utf8'))) offenders.push(path)
    }
  }
  walk(root)
  assert.deepEqual(offenders, [])
})

test('structured modified time is the publish time unless the body changed after publishing', () => {
  // 발행 전 편집(예약 글을 고친 시각)은 수정이 아니다 — dateModified가 datePublished보다 앞서면 안 된다.
  assert.equal(
    contentModifiedAt({ published_at: PUBLISHED_0800_KST, body_updated_at: '2026-10-30T05:00:00+00:00' }),
    PUBLISHED_0800_KST,
  )
  // 같은 날이라도 발행 뒤에 고쳤으면 그 시각이다(화면은 날짜 단위라 싣지 않는다).
  const sameDayLater = '2026-11-02T01:00:00+00:00'
  assert.equal(contentModifiedAt({ published_at: PUBLISHED_0800_KST, body_updated_at: sameDayLater }), sameDayLater)
  assert.equal(contentModifiedAt({ published_at: PUBLISHED_0800_KST, body_updated_at: null }), PUBLISHED_0800_KST)
  assert.equal(contentModifiedAt({ published_at: null, body_updated_at: sameDayLater }), sameDayLater)
  assert.equal(contentModifiedAt({ published_at: null, body_updated_at: null }), undefined)
  assert.equal(contentModifiedAt({ published_at: PUBLISHED_0800_KST, body_updated_at: 'not a date' }), PUBLISHED_0800_KST)
})

test('structured data, sitemap and llms.txt take the modified time and dates from clinic-dates', () => {
  const root = join(dirname(fileURLToPath(import.meta.url)), '..')
  const files = [
    join(root, 'app', '[slug]', 'contents', '[contentId]', 'page.tsx'),
    join(root, 'app', '[slug]', 'llms.txt', 'route.ts'),
    join(root, 'lib', 'sitemap-builder.ts'),
  ]
  for (const path of files) {
    const source = readFileSync(path, 'utf8')
    // 발행 전 편집 시각을 그대로 수정 시각으로 쓰지 않는다.
    assert.doesNotMatch(source, /body_updated_at\s*\|\|/, path)
    // UTC로 날짜를 자르지 않는다(08:00 KST 발행이 하루 전이 된다).
    assert.doesNotMatch(source, /toISOString\(\)\.split\('T'\)/, path)
  }
})
