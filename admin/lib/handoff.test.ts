import assert from 'node:assert/strict'
import test from 'node:test'
import {
  acceptanceDecision,
  acceptancePayload,
  contractPayload,
  defaultAcquisitionDates,
  handoffNextAction,
  koreanDateInputValue,
  koreanDateTimeLocalInputValue,
  parseOnboardingCreateRequestId,
  parseOnboardingWorkflowCheckpoint,
  serializeOnboardingWorkflowCheckpoint,
} from './handoff.ts'

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
  assert.equal(handoffNextAction({ state: 'HANDOFF_ACCEPTED' } as never), '병원 기본 정보 입력')
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

test('contract defaults follow the Korean business date and roll over the month', () => {
  assert.deepEqual(defaultAcquisitionDates(new Date('2026-08-31T15:30:00Z')), {
    effectiveDate: '2026-09-01',
    slaDueAt: '2026-09-02T18:00',
  })
  assert.deepEqual(defaultAcquisitionDates(new Date('2026-12-31T14:59:00Z')), {
    effectiveDate: '2026-12-31',
    slaDueAt: '2027-01-01T18:00',
  })
})

test('saved contract instants restore into Korean date controls', () => {
  assert.equal(koreanDateInputValue('2026-08-10T15:00:00Z'), '2026-08-11')
  assert.equal(koreanDateTimeLocalInputValue('2026-08-11T09:00:00Z'), '2026-08-11T18:00')
  assert.equal(koreanDateInputValue('invalid'), null)
})

test('workflow checkpoint persists opaque ids only and rejects corrupt values', () => {
  const checkpoint = {
    hospitalId: 'b1400000-0000-4000-8000-000000000001',
    handoffId: 'c1400000-0000-4000-8000-000000000001',
  }
  const encoded = serializeOnboardingWorkflowCheckpoint(checkpoint)

  assert.deepEqual(parseOnboardingWorkflowCheckpoint(encoded), checkpoint)
  assert.deepEqual(Object.keys(JSON.parse(encoded)).sort(), ['handoffId', 'hospitalId'])
  assert.equal(parseOnboardingWorkflowCheckpoint('{broken'), null)
  assert.equal(parseOnboardingWorkflowCheckpoint(JSON.stringify({
    ...checkpoint,
    hospitalId: '../other-hospital',
  })), null)
  assert.equal(parseOnboardingCreateRequestId(checkpoint.hospitalId), checkpoint.hospitalId)
  assert.equal(parseOnboardingCreateRequestId('../other-hospital'), null)
})
