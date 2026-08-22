// P-A-1: /visit의 "진료시간 보기"가 자기 자신을 가리키고, 정작 그 페이지에는
// 요일별 진료시간 표가 없었다. 표는 JSON-LD와 같은 business_hours에서 나와야 하고,
// 버튼은 그 표가 실제로 있는 곳을 가리켜야 한다.
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  buildOpeningHoursSpec,
  buildWeeklyHoursRows,
  hasWeeklyHours,
  VISIT_HOURS_ANCHOR,
  visitHoursHref,
} from './business-hours.ts'

const HOURS = {
  mon: '09:00-18:00',
  tue: '09:00-18:00',
  wed: '09:00-18:00',
  thu: '09:00-18:00',
  fri: '09:00-18:00',
  sat: '09:00-13:00',
  sun: '휴진',
}

test('buildWeeklyHoursRows always returns Monday through Sunday in order', () => {
  const rows = buildWeeklyHoursRows(HOURS)

  assert.deepEqual(
    rows.map((row) => row.key),
    ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'],
  )
  assert.deepEqual(
    rows.map((row) => row.label),
    ['월요일', '화요일', '수요일', '목요일', '금요일', '토요일', '일요일'],
  )
  assert.equal(rows[5].value, '09:00-13:00')
  assert.equal(rows[6].closed, true)
  assert.equal(rows[0].closed, false)
})

test('a day the hospital did not provide stays empty instead of inventing hours', () => {
  const rows = buildWeeklyHoursRows({ mon: '09:00-18:00', sat: '   ' })

  assert.equal(rows[0].value, '09:00-18:00')
  assert.equal(rows[1].value, null)
  assert.equal(rows[5].value, null)
  assert.equal(hasWeeklyHours({ mon: '09:00-18:00' }), true)
})

test('no hours at all is reported as such, for null, undefined and empty maps', () => {
  const empties: Array<Record<string, string> | null | undefined> = [null, undefined, {}, { mon: '' }]
  for (const hours of empties) {
    assert.equal(hasWeeklyHours(hours), false)
    assert.equal(buildWeeklyHoursRows(hours).length, 7)
  }
})

test('the visible table and the structured data read the same source', () => {
  const rows = buildWeeklyHoursRows(HOURS).filter((row) => row.value !== null)
  const spec = buildOpeningHoursSpec(HOURS)

  // 구조화 데이터의 description은 표에 그대로 보이는 문구여야 한다.
  const descriptions = new Set(spec.map((entry) => entry.description))
  for (const row of rows) {
    assert.ok(descriptions.has(row.value), `${row.label} 문구가 구조화 데이터와 다르다`)
  }
})

test('the hours button points at the table, never back at the same page', () => {
  const root = 'https://ai.no1top365.co.kr'

  assert.equal(visitHoursHref(root, false), `${root}/visit#${VISIT_HOURS_ANCHOR}`)
  assert.equal(visitHoursHref(root, true), `#${VISIT_HOURS_ANCHOR}`)
  assert.notEqual(visitHoursHref(root, true), `${root}/visit`)
})

const visitPage = readFileSync(new URL('../app/[slug]/visit/page.tsx', import.meta.url), 'utf8')
const hoursTable = readFileSync(
  new URL('../app/[slug]/_components/VisitHoursTable.tsx', import.meta.url),
  'utf8',
)
const contactCard = readFileSync(
  new URL('../app/[slug]/_components/ContactCard.tsx', import.meta.url),
  'utf8',
)

test('the visit page renders the hours table at the anchor the button targets', () => {
  assert.match(visitPage, /<VisitHoursTable/)
  assert.match(visitPage, /hoursHref=\{visitHoursHref\(hospitalRootUrl, true\)\}/)
  assert.match(hoursTable, /id=\{VISIT_HOURS_ANCHOR\}/)
})

test('the hours section is a real table with weekday row headers', () => {
  assert.match(hoursTable, /<table className="clinic-hours-table">/)
  assert.match(hoursTable, /<th scope="col">요일<\/th>/)
  assert.match(hoursTable, /<th scope="col">진료시간<\/th>/)
  assert.match(hoursTable, /<th scope="row">\{row\.label\}<\/th>/)
})

test('the button becomes an in-page anchor rather than a link to the current URL', () => {
  assert.match(contactCard, /const hoursIsSamePageAnchor = hoursHref\.startsWith\('#'\)/)
  assert.match(contactCard, /<a href=\{hoursHref\} className="clinic-visit-action">/)
  // 옛 자기 링크가 남아 있으면 안 된다.
  assert.doesNotMatch(contactCard, /href=\{`\$\{hospitalRootUrl\}\/visit`\}\s*className="clinic-visit-action">\s*<CalendarIcon/)
})
