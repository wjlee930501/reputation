import assert from 'node:assert/strict'
import test from 'node:test'
import { acceptanceDecision, acceptancePayload, contractPayload, handoffNextAction } from './handoff.ts'

test('contract payload carries CAS version and never acceptance facts', () => {
  const payload = contractPayload({
    salesOwnerId: 'sales', aeOwnerId: 'ae', contractReference: ' CTR-1 ',
    contractEffectiveAt: '2026-08-10T00:00:00+09:00', slaDueAt: '2026-08-11T18:00:00+09:00',
    plan: 'PLAN_12',
  }, 3)
  assert.deepEqual(payload, {
    version: 3, contract_reference: 'CTR-1',
    contract_effective_at: '2026-08-10T00:00:00+09:00', plan: 'PLAN_12',
    sla_due_at: '2026-08-11T18:00:00+09:00',
  })
  assert.equal('accepted_at' in payload, false)
  assert.equal('accepted_by_id' in payload, false)
})

test('accepted handoff exposes exactly the profile next action', () => {
  assert.equal(handoffNextAction({ state: 'HANDOFF_ACCEPTED' } as never), '병원 프로파일 입력')
  assert.deepEqual(acceptancePayload(4), { version: 4 })
})

test('assigned operator can self-accept without an override reason', () => {
  assert.deepEqual(acceptanceDecision({
    actorId: 'ae-1', actorRole: 'OPERATOR', aeOwnerId: 'ae-1', reason: '',
  }), { kind: 'ready' })
})

test('owner cross-AE acceptance requires a visible audited reason', () => {
  assert.deepEqual(acceptanceDecision({
    actorId: 'owner-1', actorRole: 'OWNER', aeOwnerId: 'ae-1', reason: ' ',
  }), {
    kind: 'blocked',
    message: '다른 AE 대신 인수 승인하는 사유를 입력해 주세요.',
  })
  assert.deepEqual(acceptanceDecision({
    actorId: 'owner-1', actorRole: 'OWNER', aeOwnerId: 'ae-1', reason: 'AE 휴가 대행',
  }), { kind: 'ready', reason: 'AE 휴가 대행' })
})
