import assert from 'node:assert/strict'
import test from 'node:test'

import { safeExternalHref } from './safe-url.ts'

test('safeExternalHref allows http and https URLs only', () => {
  assert.equal(safeExternalHref('https://example.com/path'), 'https://example.com/path')
  assert.equal(safeExternalHref('http://example.com/path'), 'http://example.com/path')
})

test('safeExternalHref rejects script and malformed URLs', () => {
  assert.equal(safeExternalHref('javascript:alert(1)'), null)
  assert.equal(safeExternalHref('/relative/path'), null)
  assert.equal(safeExternalHref('not a url'), null)
})
