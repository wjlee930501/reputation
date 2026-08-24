import assert from 'node:assert/strict'
import test from 'node:test'

import {
  hospitalMatchesStatus,
  hospitalStatusCounts,
  type HospitalStatusFilter,
} from './hospital-list-filter.ts'
import type { Hospital } from '../types/index.ts'

function hospital(id: string, status: Hospital['status'], visual = 0): Hospital {
  return {
    id,
    name: id,
    slug: id,
    status,
    plan: null,
    profile_complete: false,
    visual_approval_missing: Array.from({ length: visual }, (_, index) => `visual-${index}`),
    v0_report_done: false,
    site_live: false,
    schedule_set: false,
    created_at: null,
  }
}

test('hospital status chips count and filter the exact same row sets', () => {
  const rows = [
    hospital('active', 'ACTIVE'),
    hospital('active-review', 'ACTIVE', 1),
    hospital('onboarding', 'ONBOARDING'),
    hospital('paused', 'PAUSED'),
  ]

  assert.deepEqual(hospitalStatusCounts(rows), { total: 4, active: 2, onboarding: 1 })
  const ids = (filter: HospitalStatusFilter) =>
    rows.filter((row) => hospitalMatchesStatus(row, filter)).map((row) => row.id)
  assert.deepEqual(ids('active'), ['active', 'active-review'])
  assert.deepEqual(ids('onboarding'), ['onboarding'])
  assert.deepEqual(ids('all'), ['active', 'active-review', 'onboarding', 'paused'])
})
