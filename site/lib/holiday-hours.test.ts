// 2026-11-18 마케팅 요청(365서귀포삼성정형외과 원장님): 공휴일 휴진이 AI가 읽는 구조화 데이터에
// 없어서 병원 이름의 '365' 때문에 연중무휴로 오해받는다. 공휴일은 business_hours의 `holiday`
// 문자열 하나로 저장하고, JSON-LD에는 dayOfWeek PublicHolidays로 낸다. 값이 없으면 모든
// 출력이 도입 전과 같아야 한다.
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  buildOpeningHoursSpec,
  buildWeeklyHoursRows,
  closedDaysPhrase,
  hasWeeklyHours,
  holidayHours,
  nextOpenDay,
  uniformWeeklyHours,
  weekendHoursCard,
  weeklyRestDayLabel,
} from './business-hours.ts'
import { resolveClinicAccessMode } from './clinic-design.ts'
import { llmsBusinessHoursLines } from './llms-text.ts'

const EVERY_DAY = '09:00 ~ 18:00'
const SEVEN_DAYS = {
  mon: EVERY_DAY,
  tue: EVERY_DAY,
  wed: EVERY_DAY,
  thu: EVERY_DAY,
  fri: EVERY_DAY,
  sat: EVERY_DAY,
  sun: EVERY_DAY,
}

test('a closed public holiday becomes a PublicHolidays spec with 00:00~00:00', () => {
  const specs = buildOpeningHoursSpec({ ...SEVEN_DAYS, holiday: '휴진' })
  const holiday = specs.filter((spec) => spec.dayOfWeek === 'https://schema.org/PublicHolidays')

  assert.deepEqual(holiday, [
    {
      '@type': 'OpeningHoursSpecification',
      dayOfWeek: 'https://schema.org/PublicHolidays',
      description: '휴진',
      opens: '00:00',
      closes: '00:00',
    },
  ])
  // 요일 7개는 그대로다.
  assert.equal(specs.length, 8)
})

test('public holiday hours become a PublicHolidays spec with opens/closes', () => {
  const specs = buildOpeningHoursSpec({ mon: EVERY_DAY, holiday: '09:00 ~ 13:00' })

  assert.deepEqual(specs.at(-1), {
    '@type': 'OpeningHoursSpecification',
    dayOfWeek: 'https://schema.org/PublicHolidays',
    description: '09:00 ~ 13:00',
    opens: '09:00',
    closes: '13:00',
  })
})

test('a missing or blank holiday leaves every output exactly as before', () => {
  for (const blank of [undefined, '', '   ']) {
    const hours: Record<string, string> = { ...SEVEN_DAYS, sun: '휴진' }
    if (blank !== undefined) hours.holiday = blank
    const legacy = { ...SEVEN_DAYS, sun: '휴진' }

    assert.deepEqual(buildOpeningHoursSpec(hours), buildOpeningHoursSpec(legacy))
    assert.deepEqual(buildWeeklyHoursRows(hours), buildWeeklyHoursRows(legacy))
    assert.deepEqual(llmsBusinessHoursLines(hours), llmsBusinessHoursLines(legacy))
    assert.equal(holidayHours(hours), null)
  }
  // 공휴일 값이 없는 7일 같은 시간 병원은 종전처럼 연중무휴다.
  assert.equal(weeklyRestDayLabel(SEVEN_DAYS), '연중무휴')
  assert.equal(uniformWeeklyHours(SEVEN_DAYS), EVERY_DAY)
})

test('a hospital open seven days but closed on public holidays is not shown as 연중무휴', () => {
  const hours = { ...SEVEN_DAYS, holiday: '휴진' }

  assert.equal(uniformWeeklyHours(hours), null)
  assert.equal(weeklyRestDayLabel(hours), '공휴일')
})

test('a hospital open on public holidays too stays 연중무휴', () => {
  assert.equal(weeklyRestDayLabel({ ...SEVEN_DAYS, holiday: EVERY_DAY }), '연중무휴')
  assert.equal(weeklyRestDayLabel({ ...SEVEN_DAYS, holiday: '09:00 ~ 13:00' }), '연중무휴')
})

test('the rest-day label is only for seven equal open days', () => {
  assert.equal(weeklyRestDayLabel({ ...SEVEN_DAYS, sun: '휴진', holiday: '휴진' }), null)
  assert.equal(weeklyRestDayLabel({ ...SEVEN_DAYS, sat: '09:00 ~ 13:00', holiday: '휴진' }), null)
  assert.equal(weeklyRestDayLabel(null), null)
})

test('the weekly table and llms.txt get a 공휴일 row after Sunday', () => {
  const hours = { ...SEVEN_DAYS, holiday: '휴진' }
  const rows = buildWeeklyHoursRows(hours)

  assert.deepEqual(rows.at(-1), { key: 'holiday', label: '공휴일', value: '휴진', closed: true })
  assert.equal(rows.length, 8)
  assert.deepEqual(llmsBusinessHoursLines(hours).slice(-2), ['- 공휴일: 휴진', ''])
})

test('a holiday value alone does not count as published weekly hours', () => {
  assert.equal(hasWeeklyHours({ holiday: '휴진' }), false)
  assert.equal(hasWeeklyHours({ mon: EVERY_DAY, holiday: '휴진' }), true)
})

test('weekday maths (next open day, weekend card) ignore the holiday key', () => {
  const hours = { ...SEVEN_DAYS, sun: '휴진', holiday: '휴진' }
  const legacy = { ...SEVEN_DAYS, sun: '휴진' }

  assert.deepEqual(nextOpenDay(hours, 'sun'), nextOpenDay(legacy, 'sun'))
  assert.deepEqual(weekendHoursCard(hours), weekendHoursCard(legacy))
})

test('the closed-days notice never says 공휴일요일', () => {
  assert.equal(closedDaysPhrase(['일'], false), '일요일')
  assert.equal(closedDaysPhrase(['토', '일'], false), '토, 일요일')
  assert.equal(closedDaysPhrase(['일'], true), '일요일, 공휴일')
  assert.equal(closedDaysPhrase([], true), '공휴일')
  assert.equal(closedDaysPhrase([], false), null)
})

test('filling the holiday field does not flip the clinic access mode', () => {
  const input = {
    specialties: ['정형외과'],
    boardCertifications: [],
    businessHours: { mon: EVERY_DAY },
  }
  assert.equal(resolveClinicAccessMode(input), 'appointment')
  assert.equal(
    resolveClinicAccessMode({ ...input, businessHours: { mon: EVERY_DAY, holiday: '09:00 ~ 21:00' } }),
    'appointment',
  )
})

test('every weekly-hours renderer shows the holiday value', () => {
  // 화면 컴포넌트는 node 테스트로 그리지 못한다 — 같은 원본(holidayHours·buildWeeklyHoursRows)을
  // 읽는지만 확인한다. 레이아웃마다 빠지면 한 레이아웃의 병원만 공휴일이 안 보인다.
  const read = (path: string) => readFileSync(new URL(path, import.meta.url), 'utf8')
  for (const path of [
    '../app/[slug]/_components/WeekHours.tsx',
    '../app/[slug]/_components/HospitalFacts.tsx',
    '../app/[slug]/_layouts/HomeLayoutA.tsx',
    '../app/[slug]/_layouts/HomeLayoutC.tsx',
  ]) {
    assert.match(read(path), /holidayHours\(/, path)
  }
  assert.match(read('../app/[slug]/_components/VisitHoursTable.tsx'), /buildWeeklyHoursRows\(/)
  assert.match(read('../app/[slug]/_components/HeroToday.tsx'), /weeklyRestDayLabel\(/)
})
