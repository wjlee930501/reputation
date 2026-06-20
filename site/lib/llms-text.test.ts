import assert from 'node:assert/strict'
import test from 'node:test'

import { llmsTextValue, llmsUrlValue } from './llms-text.ts'

test('llms text values remove control characters and markdown line injection', () => {
  assert.equal(llmsTextValue('좋은 병원\n- injected: yes\r\t'), '좋은 병원 - injected: yes')
  assert.equal(llmsTextValue('Name [x](https://evil.example)'), 'Name xhttps://evil.example')
})

test('llms URL values allow only absolute http URLs on one line', () => {
  assert.equal(llmsUrlValue('https://clinic.example.com/a b'), 'https://clinic.example.com/a%20b')
  assert.equal(llmsUrlValue('javascript:alert(1)'), null)
  assert.equal(llmsUrlValue('https://clinic.example.com/\n- injected: yes'), 'https://clinic.example.com/')
})
