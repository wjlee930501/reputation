import assert from 'node:assert/strict'
import test from 'node:test'

import { publicFetchInit } from './fetch-policy.ts'

test('publicFetchInit disables stale data caching in development', () => {
  const originalNodeEnv = process.env.NODE_ENV
  process.env.NODE_ENV = 'development'

  try {
    assert.deepEqual(publicFetchInit(1800), { cache: 'no-store' })
  } finally {
    process.env.NODE_ENV = originalNodeEnv
  }
})

test('publicFetchInit keeps ISR revalidation outside development', () => {
  const originalNodeEnv = process.env.NODE_ENV
  process.env.NODE_ENV = 'production'

  try {
    assert.deepEqual(publicFetchInit(1800), { next: { revalidate: 1800 } })
  } finally {
    process.env.NODE_ENV = originalNodeEnv
  }
})
