import assert from 'node:assert/strict'
import test from 'node:test'

import { buildLeadOnboardingHref, readClinicNameFromLeadContext } from './lead-onboarding.ts'

test('manual onboarding URL contains only the opaque lead id', () => {
  const href = buildLeadOnboardingHref('lead-id')
  const url = new URL(href, 'https://admin.example.test')
  assert.equal(url.searchParams.get('leadId'), 'lead-id')
  assert.deepEqual([...url.searchParams.keys()], ['leadId'])
  assert.doesNotMatch(href, /contact|question|source|name|type/i)
})

test('clinic name is read from the authenticated lead context response, not the URL', () => {
  assert.equal(readClinicNameFromLeadContext({ lead: { clinic_name: ' 장편한외과 ' } }), '장편한외과')
  assert.equal(readClinicNameFromLeadContext({ lead: { clinic_name: '' } }), null)
  assert.equal(readClinicNameFromLeadContext({ candidates: [] }), null)
})
