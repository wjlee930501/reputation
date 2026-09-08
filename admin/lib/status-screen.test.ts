import assert from 'node:assert/strict'
import test from 'node:test'

import {
  ACTION_DISABLED_REASON,
  actionDisabledReason,
  canSubmitAssign,
  cardButtons,
  monthSummaryLines,
  mutationBodyFor,
  sameCauseNotice,
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

test('못 하는 이유는 행동마다 다르게 적는다', () => {
  // 한 문장으로 뭉뚱그리면 운영자는 권한 문제인지 아직 때가 아닌지 구분하지 못한다.
  assert.equal(
    actionDisabledReason('RECOVER_INCIDENT'),
    '연결된 작업이 아직 성공하지 않아 복구를 확인할 수 없습니다',
  )
  assert.equal(actionDisabledReason('ASSIGN_INCIDENT'), '담당 지정은 OWNER만 할 수 있습니다')
  assert.equal(actionDisabledReason('RETRY_RUN'), '담당자 또는 OWNER만 처리할 수 있습니다')
  assert.equal(actionDisabledReason('ACK_INCIDENT'), '담당자 또는 OWNER만 처리할 수 있습니다')
  // 서버가 새 종류를 늘려도 문구가 비지 않는다.
  assert.equal(actionDisabledReason('SOMETHING_NEW'), ACTION_DISABLED_REASON)
})

test('카드 버튼의 사유도 행동마다 다른 문구를 쓴다', () => {
  const buttons = cardButtons({
    actions: [
      action('RECOVER_INCIDENT', { enabled: false }),
      action('ASSIGN_INCIDENT', { enabled: false }),
    ],
  })

  assert.deepEqual(
    buttons.disabled.map((item) => item.reason),
    [
      '연결된 작업이 아직 성공하지 않아 복구를 확인할 수 없습니다',
      '담당 지정은 OWNER만 할 수 있습니다',
    ],
  )
})

test('같은 원인이 여러 건이면 대표 인시던트에만 적용된다고 말한다', () => {
  assert.equal(sameCauseNotice(undefined), null)
  assert.equal(sameCauseNotice(1), null)
  assert.equal(sameCauseNotice(4), '같은 원인 4건 — 대표 인시던트에 적용됩니다')
})

test('담당 지정은 상세를 읽기 전에는 보내지 않는다', () => {
  // 요청 본문이 지금 걸린 처리 기한을 그대로 실어야 하므로(`mutationBodyFor`),
  // 상세를 못 읽은 채 보내면 걸려 있던 기한이 지워진다.
  const base = { kind: 'ASSIGN_INCIDENT', busy: false, reason: '담당 이관' }

  assert.equal(canSubmitAssign({ ...base, detailLoaded: false, detailFailed: false }), false)
  assert.equal(canSubmitAssign({ ...base, detailLoaded: false, detailFailed: true }), false)
  assert.equal(canSubmitAssign({ ...base, detailLoaded: true, detailFailed: false }), true)
  // 사유는 3자 이상, 처리 중에는 두 번 누르지 않는다.
  assert.equal(
    canSubmitAssign({ ...base, reason: '  가 ', detailLoaded: true, detailFailed: false }),
    false,
  )
  assert.equal(
    canSubmitAssign({ ...base, busy: true, detailLoaded: true, detailFailed: false }),
    false,
  )
  // 다른 행동은 상세를 기다릴 이유가 없다.
  assert.equal(
    canSubmitAssign({
      kind: 'RETRY_RUN',
      busy: false,
      reason: '공급자 오류',
      detailLoaded: false,
      detailFailed: false,
    }),
    true,
  )
})
