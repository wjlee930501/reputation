import assert from 'node:assert/strict'
import test from 'node:test'

import { getDomainProfileResetState } from './domain-profile-reset.ts'

test('profile reset initializes defaults for a newly loaded hospital profile', () => {
  const state = getDomainProfileResetState(
    'hospital-1',
    {
      id: 'profile-1',
      aeo_domain: 'ai.clinic.example',
      domain_management_mode: 'MOTIONLABS_MANAGED',
      domain_dns_strategy: 'APEX_ADDRESS',
      domain_registrar: 'Gabia',
      domain_dns_provider: 'Cloudflare',
      domain_purchase_note: 'Renew annually',
    },
    null,
  )

  assert.deepEqual(state, {
    profileKey: 'profile-1',
    domainSavedValue: 'ai.clinic.example',
    managementMode: 'MOTIONLABS_MANAGED',
    dnsStrategy: 'APEX_ADDRESS',
    registrar: 'Gabia',
    dnsProvider: 'Cloudflare',
    purchaseNote: 'Renew annually',
  })
})

test('profile reset skips same profile key so unsaved metadata edits survive prop refreshes', () => {
  const state = getDomainProfileResetState(
    'hospital-1',
    {
      id: 'profile-1',
      aeo_domain: 'ai.clinic.example',
      domain_management_mode: 'HOSPITAL_MANAGED',
      domain_dns_strategy: 'CNAME',
      domain_registrar: 'Server value after refresh',
      domain_dns_provider: 'Server provider after refresh',
      domain_purchase_note: 'Server note after refresh',
    },
    'profile-1',
  )

  assert.equal(state, null)
})

test('profile reset falls back to hospital id before the backend profile id exists', () => {
  const state = getDomainProfileResetState('hospital-1', {}, null)

  assert.deepEqual(state, {
    profileKey: 'hospital-1',
    domainSavedValue: '',
    managementMode: 'HOSPITAL_MANAGED',
    dnsStrategy: 'CNAME',
    registrar: '',
    dnsProvider: '',
    purchaseNote: '',
  })
})
