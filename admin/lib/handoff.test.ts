import assert from 'node:assert/strict'
import test from 'node:test'
import { acceptancePayload, contractPayload, handoffNextAction } from './handoff.ts'

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
