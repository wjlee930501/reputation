import assert from 'node:assert/strict'
import test from 'node:test'

import { publicFetchInit } from './fetch-policy.ts'

const mutableEnv = process.env as Record<string, string | undefined>

test('publicFetchInit disables stale data caching in development', () => {
  const originalNodeEnv = process.env.NODE_ENV
  mutableEnv.NODE_ENV = 'development'

  try {
    assert.deepEqual(publicFetchInit(1800), { cache: 'no-store' })
  } finally {
    mutableEnv.NODE_ENV = originalNodeEnv
  }
})

test('publicFetchInit keeps ISR revalidation outside development', () => {
  const originalNodeEnv = process.env.NODE_ENV
  mutableEnv.NODE_ENV = 'production'

  try {
    assert.deepEqual(publicFetchInit(1800), { next: { revalidate: 1800 } })
  } finally {
    mutableEnv.NODE_ENV = originalNodeEnv
  }
})
