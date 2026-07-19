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
