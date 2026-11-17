import assert from 'node:assert/strict'
import test from 'node:test'

import { buildOpeningHoursSpec, extractTimeRanges } from './business-hours.ts'

test('lunch break note does not replace the actual clinic closing time', () => {
  assert.deepEqual(extractTimeRanges('08:30-18:00 (13:00-14:00 점심)'), [
    { opens: '08:30', closes: '18:00' },
  ])

  assert.deepEqual(buildOpeningHoursSpec({ mon: '08:30-18:00 (13:00-14:00 점심)' }), [
    {
      '@type': 'OpeningHoursSpecification',
      dayOfWeek: 'Monday',
      description: '08:30-18:00 (13:00-14:00 점심)',
      opens: '08:30',
      closes: '18:00',
    },
  ])
})

test('split morning and afternoon clinic ranges remain separate', () => {
  assert.deepEqual(extractTimeRanges('09:00-12:00 / 13:00-18:00'), [
    { opens: '09:00', closes: '12:00' },
    { opens: '13:00', closes: '18:00' },
  ])
})

test('주말 칸: 토·일 같은 시간이면 한 줄, 다르면 함께, 일요일 휴진이면 토요일만', async () => {
  const { weekendHoursCard } = await import('./business-hours.ts')
  const same = '08:30 ~ 13:00 (점심시간 없음, 접수 마감 12:30)'
  assert.deepEqual(weekendHoursCard({ sat: same, sun: same }), { label: '토·일요일 진료', value: same, sunday: null })
  assert.deepEqual(weekendHoursCard({ sat: '09:00 ~ 13:00', sun: '10:00 ~ 12:00' }), {
    label: '토·일요일 진료',
    value: '09:00 ~ 13:00',
    sunday: '10:00 ~ 12:00',
  })
  assert.deepEqual(weekendHoursCard({ sat: '09:00 ~ 13:00', sun: '휴진' }), { label: '토요일 진료', value: '09:00 ~ 13:00', sunday: null })
  assert.deepEqual(weekendHoursCard({ sat: '09:00 ~ 13:00' }), { label: '토요일 진료', value: '09:00 ~ 13:00', sunday: null })
  assert.deepEqual(weekendHoursCard({ sat: '휴진', sun: '09:00 ~ 12:00' }), { label: '일요일 진료', value: '09:00 ~ 12:00', sunday: null })
  assert.deepEqual(weekendHoursCard({ sat: '휴진', sun: '휴진' }), { label: '토요일 진료', value: '휴진', sunday: null })
  assert.deepEqual(weekendHoursCard(null), { label: '토요일 진료', value: null, sunday: null })
})
