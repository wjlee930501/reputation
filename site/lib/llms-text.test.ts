import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import { buildOpeningHoursSpec } from './business-hours.ts'
import { llmsBusinessHoursLines, llmsTextValue, llmsUrlValue } from './llms-text.ts'

const ROUTE = readFileSync(
  join(dirname(fileURLToPath(import.meta.url)), '..', 'app', '[slug]', 'llms.txt', 'route.ts'),
  'utf8',
)

test('llms text values remove control characters and markdown line injection', () => {
  assert.equal(llmsTextValue('좋은 병원\n- injected: yes\r\t'), '좋은 병원 - injected: yes')
  assert.equal(llmsTextValue('Name [x](https://evil.example)'), 'Name xhttps://evil.example')
})

test('llms URL values allow only absolute http URLs on one line', () => {
  assert.equal(llmsUrlValue('https://clinic.example.com/a b'), 'https://clinic.example.com/a%20b')
  assert.equal(llmsUrlValue('javascript:alert(1)'), null)
  assert.equal(llmsUrlValue('https://clinic.example.com/\n- injected: yes'), 'https://clinic.example.com/')
})

test('llms.txt lists weekday hours in the fixed weekday order', () => {
  const lines = llmsBusinessHoursLines({
    sat: '09:00-13:00',
    mon: '09:00-18:00',
    sun: '휴진',
  })

  assert.deepEqual(lines, [
    '## 진료시간',
    '- 월요일: 09:00-18:00',
    '- 토요일: 09:00-13:00',
    '- 일요일: 휴진',
    '',
  ])
})

test('llms.txt invents no hours for days the clinic left blank', () => {
  assert.deepEqual(llmsBusinessHoursLines(null), [])
  assert.deepEqual(llmsBusinessHoursLines({}), [])
  assert.deepEqual(llmsBusinessHoursLines({ mon: '   ' }), [])
})

test('llms.txt hours are sanitized like every other llms value', () => {
  const lines = llmsBusinessHoursLines({ mon: '09:00-18:00\n- injected: yes' })
  assert.deepEqual(lines, ['## 진료시간', '- 월요일: 09:00-18:00 - injected: yes', ''])
})

test('llms.txt and the JSON-LD opening hours read the same days from one source', () => {
  // P-A-6 — 화면·구조화 데이터·llms.txt가 서로 다른 진료시간을 말할 수 없어야 한다.
  const hours = { mon: '09:00-18:00', tue: '09:00-18:00', sun: '휴진' }
  const llmsDays = llmsBusinessHoursLines(hours)
    .filter((line) => line.startsWith('- '))
    .map((line) => line.slice(2).split(':')[0])
  const schemaDays = new Set(
    buildOpeningHoursSpec(hours).map((spec) => String(spec.dayOfWeek)),
  )

  assert.equal(llmsDays.length, schemaDays.size)
  assert.match(ROUTE, /llmsBusinessHoursLines\(hospital\.business_hours\)/)
})
