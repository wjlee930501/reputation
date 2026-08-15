import assert from 'node:assert/strict'
import test from 'node:test'

import { customDomainLiveUrl } from './domain-live-links.ts'

test('custom domain live URL requires a saved non-empty custom domain', () => {
  assert.equal(customDomainLiveUrl({ site_live: true, aeo_domain: ' clinic.example.com ', hasUnsavedChange: false }), 'https://clinic.example.com')
  assert.equal(customDomainLiveUrl({ site_live: true, aeo_domain: null, hasUnsavedChange: false }), null)
  assert.equal(customDomainLiveUrl({ site_live: true, aeo_domain: '   ', hasUnsavedChange: false }), null)
  assert.equal(customDomainLiveUrl({ site_live: true, aeo_domain: 'clinic.example.com', hasUnsavedChange: true }), null)
  assert.equal(customDomainLiveUrl({ site_live: false, aeo_domain: 'clinic.example.com', hasUnsavedChange: false }), null)
})
