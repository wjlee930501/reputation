import assert from 'node:assert/strict'
import test from 'node:test'

import {
  ACTION_DISABLED_REASON,
  cardButtons,
  monthSummaryLines,
  mutationBodyFor,
  splitRemaining,
  stateCardCopy,
} from './status-screen.ts'
import type { RemainingCondition } from './hospital-states.ts'
import type { HospitalOverviewMonth, OperationsAction } from '../types/index.ts'

function condition(
  key: string,
  actor: 'human' | 'system',
  href: string | null = null,
): RemainingCondition {
  return { key, label: key, actor, href }
}

function action(kind: string, overrides: Partial<OperationsAction> = {}): OperationsAction {
  return {
    kind,
    label: kind,
    method: 'POST',
    path: `/api/admin/operations/${kind}`,
    enabled: true,
    ...overrides,
  }
}

function month(overrides: Partial<HospitalOverviewMonth> = {}): HospitalOverviewMonth {
  return {
    year: 2026,
    month: 9,
    published_count: 5,
    public_count: 3,
    withheld_count: 0,
    planned_total: 12,
    mention_rate: null,
    mention_rate_measured_at: null,
    next_report_date: '2026-10-01',
    ...overrides,
  }
}

test('남은 조건은 사람 몫과 시스템 몫으로 나뉜다', () => {
  const split = splitRemaining([
    condition('profile_complete', 'human', '/hospitals/1/info'),
    condition('site_built', 'system'),
    condition('schedule', 'human', '/hospitals/1/content#content-schedule'),
  ])

  assert.deepEqual(
    split.human.map((item) => item.key),
    ['profile_complete', 'schedule'],
  )
  assert.deepEqual(
    split.system.map((item) => item.key),
    ['site_built'],
  )
})

test('사람 몫이 아닌 조건은 화면에서 사라지지 않는다', () => {
  // 서버가 actor를 늘려도 조건 자체는 계속 보여야 한다.
  const split = splitRemaining([
    { key: 'future', label: 'future', actor: 'vendor' as unknown as 'system', href: null },
  ])
  assert.equal(split.human.length, 0)
  assert.equal(split.system.length, 1)
})

test('카드 머리는 서버 라벨과 남은 조건 수를 말한다', () => {
  const copy = stateCardCopy({
    label: '준비 중',
    remaining: [
      condition('profile_complete', 'human'),
      condition('schedule', 'human'),
      condition('site_built', 'system'),
    ],
  })

  assert.deepEqual(copy, { label: '준비 중', summary: '할 일 2개 · 시스템 처리 중 1개' })
})

test('남은 조건이 한쪽뿐이거나 없을 때도 한 줄로 말한다', () => {
  assert.equal(stateCardCopy({ label: '공개 중', remaining: [] }).summary, '남은 조건 없음')
  assert.equal(
    stateCardCopy({ label: '준비 중', remaining: [condition('schedule', 'human')] }).summary,
    '할 일 1개',
  )
  assert.equal(
    stateCardCopy({ label: '준비 중', remaining: [condition('site_built', 'system')] }).summary,
    '시스템 처리 중 1개',
  )
})

test('이번 달 요약은 공개·발행·계획과 다음 보고서 날짜를 말한다', () => {
  assert.deepEqual(monthSummaryLines(month()), [
    '공개 3 / 발행 5 / 계획 12편',
    '측정 결과 없음',
    '다음 보고서 10월 1일',
  ])
})

test('공개 보류는 있을 때만 한 줄을 차지하고, 언급률은 잰 날짜를 함께 말한다', () => {
  assert.deepEqual(
    monthSummaryLines(
      month({ withheld_count: 2, mention_rate: 12, mention_rate_measured_at: '2026-09-01' }),
    ),
    [
      '공개 3 / 발행 5 / 계획 12편',
      '공개 보류 2편',
      '병원 언급률 12.0% (9/1 측정)',
      '다음 보고서 10월 1일',
    ],
  )
})

test('언급률을 잰 날짜가 없으면 날짜를 지어내지 않는다', () => {
  const lines = monthSummaryLines(month({ mention_rate: 0 }))
  assert.equal(lines[1], '병원 언급률 0.0%')
})

test('버튼은 서버가 허용한 행동만, 정해진 순서로 만든다', () => {
  const buttons = cardButtons({
    actions: [
      action('OPEN_INCIDENT', { method: 'GET', path: '/operations?queue=incidents' }),
      action('ASSIGN_INCIDENT', { requires_version: true }),
      action('ACK_INCIDENT', { requires_version: true }),
      action('RETRY_RUN', { requires_idempotency_key: true }),
    ],
  })

  assert.deepEqual(
    buttons.enabled.map((item) => item.kind),
    ['RETRY_RUN', 'ACK_INCIDENT', 'ASSIGN_INCIDENT'],
  )
  assert.deepEqual(buttons.disabled, [])
})

test('지금 못 하는 행동은 버튼이 아니라 사유 문구로 남는다', () => {
  const buttons = cardButtons({
    actions: [
      action('RETRY_RUN', { enabled: false }),
      action('RECOVER_INCIDENT', { requires_version: true }),
    ],
  })

  assert.deepEqual(
    buttons.enabled.map((item) => item.kind),
    ['RECOVER_INCIDENT'],
  )
  assert.equal(buttons.disabled.length, 1)
  assert.equal(buttons.disabled[0].action.kind, 'RETRY_RUN')
  assert.equal(buttons.disabled[0].reason, ACTION_DISABLED_REASON)
})

test('요청 본문은 행동 종류가 정한다', () => {
  assert.deepEqual(
    mutationBodyFor(action('RETRY_RUN', { requires_idempotency_key: true }), {
      reason: ' 공급자 오류 재실행 ',
      version: 3,
    }),
    { reason: '공급자 오류 재실행' },
  )
  assert.deepEqual(
    mutationBodyFor(action('RECOVER_INCIDENT', { requires_version: true }), {
      reason: '복구 확인',
      version: 3,
    }),
    { expected_version: 3, reason: '복구 확인' },
  )
  assert.deepEqual(
    mutationBodyFor(action('ACK_INCIDENT', { requires_version: true }), {
      reason: '확인 완료',
      version: 4,
    }),
    { expected_version: 4, reason: '확인 완료' },
  )
})

test('담당 지정은 담당자와 지금 걸린 처리 기한을 함께 보낸다', () => {
  // 기한 필드를 빼면 서버가 거절하고, 비워 보내면 걸려 있던 기한이 지워진다.
  assert.deepEqual(
    mutationBodyFor(action('ASSIGN_INCIDENT', { requires_version: true }), {
      reason: '담당 이관',
      version: 7,
      ownerId: 'owner-1',
      slaDueAt: '2026-09-10T00:00:00Z',
    }),
    {
      expected_version: 7,
      reason: '담당 이관',
      owner_id: 'owner-1',
      sla_due_at: '2026-09-10T00:00:00Z',
    },
  )
})
