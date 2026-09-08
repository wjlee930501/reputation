import assert from 'node:assert/strict'
import test from 'node:test'

import { ADMIN_COPY, describeMentionRate, describeUnknownCount } from './admin-copy.ts'
import { STATUS_LABELS } from '../types/index.ts'

test('admin copy uses operator terms instead of implementation labels', () => {
  assert.equal(ADMIN_COPY.aiMentionRate, '병원 언급률')
  assert.equal(ADMIN_COPY.aiMentionRateDetail, 'AI 답변에서 병원이 언급된 비율')
  assert.equal(ADMIN_COPY.evidence, '근거 자료')
  assert.equal(ADMIN_COPY.observedAt, '확인 시각')
  assert.equal(ADMIN_COPY.attribution, '연결 근거')
})

test('one term per concept: new screens read these keys instead of inventing variants', () => {
  assert.equal(ADMIN_COPY.publicPage, '병원 공개 페이지')
  assert.equal(ADMIN_COPY.publicUrl, '공개 주소')
  assert.equal(ADMIN_COPY.operatingStandard, '콘텐츠 운영 기준')
  assert.equal(ADMIN_COPY.initialReport, '초기 진단 보고서')
  assert.equal(ADMIN_COPY.monthlyReport, '보고서')
  assert.equal(ADMIN_COPY.evidence, '근거 자료')
  assert.equal(ADMIN_COPY.evidenceNote, '근거 노트')
  assert.equal(ADMIN_COPY.plan, '요금제')
  assert.equal(ADMIN_COPY.monthlyArticles, '월 발행 편수')
  assert.equal(ADMIN_COPY.operator, '운영자')
  assert.equal(ADMIN_COPY.aeOwner, '담당 AE')
  assert.equal(ADMIN_COPY.postPublishReview, '공개 후 확인')
  assert.equal(ADMIN_COPY.postPublishReviewed, '확인 완료')
})

test('hospital status labels say the same thing the rest of admin says', () => {
  assert.equal(STATUS_LABELS.ANALYZING.label, '초기 진단 보고서 준비 중')
  assert.equal(STATUS_LABELS.BUILDING.label, '병원 공개 페이지 준비 중')
  assert.ok(STATUS_LABELS.ANALYZING.label.startsWith(ADMIN_COPY.initialReport))
  assert.ok(STATUS_LABELS.BUILDING.label.startsWith(ADMIN_COPY.publicPage))
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
