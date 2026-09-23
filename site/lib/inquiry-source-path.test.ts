import assert from 'node:assert/strict'
import test from 'node:test'

import { inquirySourcePath } from './inquiry-lead.ts'

test('inquirySourcePath labels /contact routes as /contact', () => {
  assert.equal(inquirySourcePath('/contact'), '/contact')
  assert.equal(inquirySourcePath('/contact/'), '/contact')
  assert.equal(inquirySourcePath('/contact/thanks'), '/contact')
})

test('inquirySourcePath labels other paths as /#contact', () => {
  assert.equal(inquirySourcePath('/'), '/#contact')
  assert.equal(inquirySourcePath('/privacy'), '/#contact')
  assert.equal(inquirySourcePath(''), '/#contact')
})
