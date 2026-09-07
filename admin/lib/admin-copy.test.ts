import assert from 'node:assert/strict'
import test from 'node:test'

import { ADMIN_COPY, describeMentionRate, describeUnknownCount } from './admin-copy.ts'

test('admin copy uses operator terms instead of implementation labels', () => {
  assert.equal(ADMIN_COPY.aiMentionRate, '병원 언급률')
  assert.equal(ADMIN_COPY.aiMentionRateDetail, 'AI 답변에서 병원이 언급된 비율')
  assert.equal(ADMIN_COPY.evidence, '근거 자료')
  assert.equal(ADMIN_COPY.observedAt, '확인 시각')
  assert.equal(ADMIN_COPY.attribution, '연결 근거')
})

test('mention rate keeps unavailable results separate from zero', () => {
  assert.equal(describeMentionRate(null), '측정 결과 없음')
  assert.equal(describeMentionRate(0), '0.0%')
})

test('unknown counts are not presented as zero', () => {
  assert.equal(describeUnknownCount(null, '건'), '건 확인 안 됨')
  assert.equal(describeUnknownCount(0, '건'), '0건')
  assert.equal(describeUnknownCount(3, '건'), '3건')
})
