import assert from 'node:assert/strict'
import test from 'node:test'

import { reportNoticeLabels } from './report-warning-labels.ts'

test('stored report codes become Korean notices; sentences pass through', () => {
  assert.deepEqual(
    reportNoticeLabels(['LEGACY_REPORT_UNVERIFIED', '승인된 콘텐츠 운영 기준이 없습니다.', 'MEASUREMENT_SLOTS_PENDING']),
    ['이전 방식 보고서 · 미검증', '승인된 콘텐츠 운영 기준이 없습니다.', '측정 진행 중'],
  )
})

test('an unknown code never reaches the screen in English', () => {
  assert.deepEqual(reportNoticeLabels(['SOMETHING_NEW', 'ANOTHER_ONE']), ['추가 확인 필요 항목'])
})
