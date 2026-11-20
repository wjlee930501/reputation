import assert from 'node:assert/strict'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { dirname, join } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import { articleDateLabels, formatSeoulDate, formatSeoulDotDate, seoulDateKey } from './clinic-dates.ts'

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
