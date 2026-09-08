import assert from 'node:assert/strict'
import test from 'node:test'

import {
  existingHospitalId,
  registrationBlockReason,
  registrationPayload,
  suggestContractReference,
  todayInKorea,
  type ContractRegistrationForm,
} from './contract-registration.ts'

function form(overrides: Partial<ContractRegistrationForm> = {}): ContractRegistrationForm {
  return {
    name: ' 장편한외과의원 ',
    leadId: 'lead-1',
    contractReference: ' RP-202609-a1b2 ',
    effectiveDate: '2026-09-09',
    plan: 'PLAN_16',
    aeOwnerId: 'ae-1',
    salesOwnerId: 'sales-1',
    ...overrides,
  }
}

test('the suggested contract reference carries the Korean year-month and a hex suffix', () => {
  // 2026-09-01 00:30 KST = 2026-08-31 15:30 UTC — 제안값은 한국 달을 따라야 한다.
  const reference = suggestContractReference(new Date('2026-08-31T15:30:00Z'), () => 0.99)
  assert.equal(reference, 'RP-202609-ffff')
  assert.match(suggestContractReference(new Date('2026-09-09T00:00:00Z'), () => 0), /^RP-202609-0000$/)
})

test('the default effective date is today in Korea', () => {
  assert.equal(todayInKorea(new Date('2026-08-31T15:30:00Z')), '2026-09-01')
  assert.equal(todayInKorea(new Date('2026-09-09T14:59:00Z')), '2026-09-09')
})

test('the payload trims operator input and sends empty owners as null', () => {
  assert.deepEqual(registrationPayload(form()), {
    name: '장편한외과의원',
    lead_id: 'lead-1',
    contract_reference: 'RP-202609-a1b2',
    contract_effective_at: '2026-09-09',
    plan: 'PLAN_16',
    ae_owner_id: 'ae-1',
    sales_owner_id: 'sales-1',
  })
  const walkIn = registrationPayload(form({ leadId: null, salesOwnerId: '' }))
  assert.equal(walkIn.lead_id, null)
  assert.equal(walkIn.sales_owner_id, null)
})

test('the block reason names the one field that is still missing', () => {
  assert.equal(registrationBlockReason(form()), null)
  assert.match(registrationBlockReason(form({ name: '  ' })) ?? '', /병원명/)
  assert.match(registrationBlockReason(form({ contractReference: '' })) ?? '', /계약 번호/)
  assert.match(registrationBlockReason(form({ effectiveDate: '' })) ?? '', /효력일/)
  assert.match(registrationBlockReason(form({ aeOwnerId: '' })) ?? '', /담당 AE/)
})

test('only a HOSPITAL_EXISTS body yields an existing hospital to open', () => {
  assert.equal(existingHospitalId({ code: 'HOSPITAL_EXISTS', hospital_id: 'h-1' }), 'h-1')
  assert.equal(existingHospitalId({ code: 'HOSPITAL_EXISTS' }), null)
  assert.equal(existingHospitalId({ code: 'CONTRACT_REFERENCE_EXISTS', hospital_id: 'h-1' }), null)
  assert.equal(existingHospitalId('nope'), null)
  assert.equal(existingHospitalId(null), null)
})
