import assert from 'node:assert/strict'
import test from 'node:test'

import {
  ROBOTS_ALLOWED_PATHS,
  ROBOTS_DISALLOWED_PATHS,
  ROBOTS_IMAGE_PROXY_ALLOW,
} from './robots-policy.ts'

test('robots leaves Next render assets crawlable while private API paths stay blocked', () => {
  assert.ok(ROBOTS_DISALLOWED_PATHS.includes('/api/'))
  assert.ok(ROBOTS_DISALLOWED_PATHS.includes('/.well-known/'))
  assert.ok(ROBOTS_DISALLOWED_PATHS.every((path) => !path.startsWith('/_next/')))
})

test('robots explicitly allows public image proxy paths', () => {
  for (const path of ROBOTS_IMAGE_PROXY_ALLOW) {
    assert.ok(ROBOTS_ALLOWED_PATHS.includes(path))
  }
})
