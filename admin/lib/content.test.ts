import assert from 'node:assert/strict'
import test from 'node:test'

import {
  belongsToMonthView,
  buildPublicContentUrl,
  countCarriedOver,
  countUnpublishedCarriedOver,
  getContentOperationsBucket,
  getContentOperationsState,
  getPublishNotificationPresentation,
  isCarriedOver,
  matchesContentOperationsFilter,
  sortCarriedOverFirst,
} from './content.ts'

test('isCarriedOver only flags items with carried_over_from set', () => {
  assert.equal(isCarriedOver({ carried_over_from: '2026-05-28' }), true)
  assert.equal(isCarriedOver({ carried_over_from: null }), false)
  assert.equal(isCarriedOver({}), false)
})

test('sortCarriedOverFirst moves carried items to the top, preserving order in both groups', () => {
  const items = [
    { id: 'a', carried_over_from: null },
    { id: 'b', carried_over_from: '2026-05-26' },
    { id: 'c', carried_over_from: null },
    { id: 'd', carried_over_from: '2026-05-28' },
  ]

  const sorted = sortCarriedOverFirst(items)

  assert.deepEqual(
    sorted.map((item) => item.id),
    ['b', 'd', 'a', 'c'],
  )
})

test('sortCarriedOverFirst keeps order untouched when nothing is carried over', () => {
  const items = [
    { id: 'a', carried_over_from: null },
    { id: 'b', carried_over_from: null },
  ]

  assert.deepEqual(
    sortCarriedOverFirst(items).map((item) => item.id),
    ['a', 'b'],
  )
})

test('countCarriedOver counts all carried items regardless of status', () => {
  const items = [
    { carried_over_from: '2026-05-26', status: 'PUBLISHED' },
    { carried_over_from: '2026-05-28', status: 'DRAFT' },
    { carried_over_from: null, status: 'DRAFT' },
  ]

  assert.equal(countCarriedOver(items), 2)
})

test('countUnpublishedCarriedOver excludes published carried items', () => {
  const items = [
    { carried_over_from: '2026-05-26', status: 'PUBLISHED' },
    { carried_over_from: '2026-05-28', status: 'DRAFT' },
    { carried_over_from: '2026-05-30', status: 'REJECTED' },
    { carried_over_from: null, status: 'DRAFT' },
  ]

  assert.equal(countUnpublishedCarriedOver(items), 2)
})

// 공개 사이트가 실제로 내보내는 중이라는 서버 판정. 발행 글의 판정은 이것이 먼저다.
const VISIBLE = {
  publishable: false,
  public_visibility: { visible: true, blockers: [], blocker_labels: [] },
}

test('content operations state distinguishes Slack retry, post-review, and reviewed states', () => {
  assert.equal(
    getContentOperationsState({
      status: 'PUBLISHED',
      compliance: VISIBLE,
      display: { review: { notification_state: 'NOT_REQUIRED' } },
    }),
    'published',
  )
  // 알림이 필요 없는 공개(수동 발행)라도 표본이면 backend 예외 큐가 센다.
  assert.equal(
    getContentOperationsState({
      status: 'PUBLISHED',
      compliance: VISIBLE,
      post_publish_review_required: true,
      display: { review: { notification_state: 'NOT_REQUIRED' } },
    }),
    'postReviewPending',
  )
  assert.equal(
    getContentOperationsState({
      status: 'PUBLISHED',
      compliance: VISIBLE,
      display: { review: { notification_state: 'PENDING' } },
    }),
    'notificationPending',
  )
  assert.equal(
    getContentOperationsState({
      status: 'PUBLISHED',
      compliance: VISIBLE,
      post_publish_review_required: true,
      display: { review: { notification_state: 'SENT' } },
    }),
    'postReviewPending',
  )
  // 표본이 아닌 글은 확인 대기로 세지 않는다 — backend 예외 큐와 같은 기준이다(M-21).
  assert.equal(
    getContentOperationsState({
      status: 'PUBLISHED',
      compliance: VISIBLE,
      display: { review: { notification_state: 'SENT' } },
    }),
    'published',
  )
  // Legacy timestamp must never override the server-authoritative outbox state.
  assert.equal(
    getContentOperationsState({
      status: 'PUBLISHED',
      compliance: VISIBLE,
      post_publish_notified_at: '2026-07-16T08:00:00Z',
      display: { review: { notification_state: 'FAILED' } },
    }),
    'notificationPending',
  )
  assert.equal(
    getContentOperationsState({
      status: 'PUBLISHED',
      compliance: VISIBLE,
      post_publish_notified_at: '2026-07-16T08:00:00Z',
      post_publish_reviewed_at: '2026-07-16T09:00:00Z',
    }),
    'published',
  )
  assert.equal(getContentOperationsState({ status: 'DRAFT', title: null }), 'notGenerated')
  assert.equal(
    getContentOperationsState({ status: 'DRAFT', title: 'blocked', compliance: { publishable: false } }),
    'needsReview',
  )
  assert.equal(
    getContentOperationsState({ status: 'DRAFT', title: 'ready', compliance: { publishable: true } }),
    'publishable',
  )
  assert.equal(getContentOperationsState({ status: 'CANCELLED', title: 'old draft' }), 'cancelled')
})

test('a withheld published item is never bucketed as published, post-review, or notification', () => {
  // 공개 사이트가 숨기는 중인 글 — 확인 기록도 알림 상태도 이 사실을 덮지 못한다(H-01).
  const withheld = (extra: Record<string, unknown> = {}) => ({
    status: 'PUBLISHED',
    compliance: {
      publishable: false,
      public_visibility: {
        visible: false,
        blockers: ['IMAGE_NOT_CERTIFIED'],
        blocker_labels: ['대표 이미지 재인증 대기'],
      },
    },
    ...extra,
  })

  assert.equal(getContentOperationsState(withheld()), 'withheld')
  assert.equal(
    getContentOperationsState(withheld({ post_publish_reviewed_at: '2026-07-16T09:00:00Z' })),
    'withheld',
  )
  for (const state of ['PENDING', 'SENT', 'NOT_REQUIRED'] as const) {
    assert.equal(
      getContentOperationsState(withheld({ display: { review: { notification_state: state } } })),
      'withheld',
    )
  }
  // 공개 중인 글의 판정은 그대로다.
  assert.equal(
    getContentOperationsState({
      status: 'PUBLISHED',
      post_publish_reviewed_at: '2026-07-16T09:00:00Z',
      compliance: {
        publishable: false,
        public_visibility: { visible: true, blockers: [], blocker_labels: [] },
      },
    }),
    'published',
  )
})

test('a published item without a visibility judgment is withheld, never published', () => {
  // 경계는 fail-closed다 — 판정이 없다는 건 "공개 중"이 아니라 "모른다"는 뜻이고,
  // 모르는 상태를 초록으로 칠하면 admin만 공개라고 말하는 H-01이 그대로 돌아온다.
  assert.equal(
    getContentOperationsState({
      status: 'PUBLISHED',
      post_publish_reviewed_at: '2026-07-16T09:00:00Z',
      compliance: { publishable: false },
    }),
    'withheld',
  )
  assert.equal(getContentOperationsState({ status: 'PUBLISHED' }), 'withheld')
  assert.equal(
    getContentOperationsBucket({ status: 'PUBLISHED', compliance: { publishable: false } }),
    'needsReview',
  )
})

test('withheld items are counted and filtered with the blocked bucket, never with published', () => {
  const item = {
    status: 'PUBLISHED',
    post_publish_reviewed_at: '2026-07-16T09:00:00Z',
    display: { review: { notification_state: 'SENT' as const } },
    compliance: {
      publishable: false,
      public_visibility: {
        visible: false,
        blockers: ['FORBIDDEN_EXPRESSION'],
        blocker_labels: ['의료광고 금지 표현 포함'],
      },
    },
  }

  assert.equal(getContentOperationsBucket(item), 'needsReview')
  assert.equal(matchesContentOperationsFilter(item, 'needsReview'), true)
  assert.equal(matchesContentOperationsFilter(item, 'published'), false)
  assert.equal(matchesContentOperationsFilter(item, 'postReviewPending'), false)
  assert.equal(matchesContentOperationsFilter(item, 'notificationPending'), false)
})

test('publish notification presentation is server-authoritative and operator-readable', () => {
  const presentation = getPublishNotificationPresentation({
    status: 'PUBLISHED',
    post_publish_notified_at: '2026-07-16T08:00:00Z',
    display: {
      review: {
        notification_state: 'FAILED',
        notification: {
          state: 'FAILED',
          label: 'Slack 전달 실패',
          problem: 'Slack에 운영 알림을 전달하지 못했습니다.',
          publication_impact: '콘텐츠 발행에는 영향이 없습니다.',
          next_action: '운영센터에서 실패 원인을 확인하고 알림을 다시 시도해 주세요.',
          notification_id: 'notification-1',
          safe_error_code: 'DELIVERY_RETRY_EXHAUSTED',
        },
      },
    },
  })

  assert.equal(presentation.state, 'FAILED')
  assert.equal(presentation.label, 'Slack 전달 실패')
  assert.match(presentation.publication_impact, /발행에는 영향이 없습니다/)
  assert.match(presentation.next_action, /운영센터/)
})

test('content operations filters support actionable summary-card filtering', () => {
  const item = {
    status: 'PUBLISHED',
    carried_over_from: '2026-06-30',
    compliance: VISIBLE,
    post_publish_notified_at: '2026-07-16T08:00:00Z',
    post_publish_review_required: true,
    display: { review: { notification_state: 'SENT' as const } },
  }
  assert.equal(matchesContentOperationsFilter(item, 'all'), true)
  assert.equal(matchesContentOperationsFilter(item, 'carried'), true)
  assert.equal(matchesContentOperationsFilter(item, 'postReviewPending'), true)
  assert.equal(matchesContentOperationsFilter(item, 'notificationPending'), false)
})

test('buildPublicContentUrl normalizes schemes, slashes, and content ids', () => {
  assert.equal(buildPublicContentUrl('https://jangclinic.kr/', 'content 1'), 'https://jangclinic.kr/contents/content%201')
  assert.equal(buildPublicContentUrl('jangclinic.kr', 'abc'), 'https://jangclinic.kr/contents/abc')
  assert.equal(buildPublicContentUrl(null, 'abc'), null)
})

test('belongsToMonthView keeps items whose scheduled_date matches the viewed year/month', () => {
  assert.equal(belongsToMonthView({ scheduled_date: '2026-09-15' }, 2026, 9), true)
  assert.equal(belongsToMonthView({ scheduled_date: '2026-09-15T00:00:00Z' }, 2026, 9), true)
})

test('belongsToMonthView drops items rejected past the last day of the month (rescheduled to next month)', () => {
  // reject_content가 오늘 이하인 scheduled_date를 내일로 재스케줄 — 월말 반려는 다음 달로 넘어간다.
  assert.equal(belongsToMonthView({ scheduled_date: '2026-10-01' }, 2026, 9), false)
})

test('belongsToMonthView is conservative about missing/unparseable dates (keeps existing merge behavior)', () => {
  assert.equal(belongsToMonthView({ scheduled_date: null }, 2026, 9), true)
  assert.equal(belongsToMonthView({}, 2026, 9), true)
  assert.equal(belongsToMonthView({ scheduled_date: 'not-a-date' }, 2026, 9), true)
})
