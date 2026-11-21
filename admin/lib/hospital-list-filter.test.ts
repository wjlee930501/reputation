import assert from 'node:assert/strict'
import test from 'node:test'

import {
  hospitalMatchesStatus,
  isContractEnded,
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
    missing_profile_requirements: [],
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
  const gaps = new Set(['active-review', 'paused'])

  assert.deepEqual(hospitalStatusCounts(rows, gaps), {
    total: 4, default: 3, active: 2, onboarding: 1, paused: 1, ended: 0, reportGap: 2,
  })
  const ids = (filter: HospitalStatusFilter) =>
    rows.filter((row) => hospitalMatchesStatus(row, filter, gaps)).map((row) => row.id)
  assert.deepEqual(ids('active'), ['active', 'active-review'])
  assert.deepEqual(ids('onboarding'), ['onboarding'])
  assert.deepEqual(ids('all'), ['active', 'active-review', 'onboarding', 'paused'])
  // 기본 목록은 일시 정지 병원을 빼고 아래 묶음으로 둔다(2026-11-20).
  assert.deepEqual(ids('default'), ['active', 'active-review', 'onboarding'])
  assert.deepEqual(ids('paused'), ['paused'])
  assert.deepEqual(ids('reportGap'), ['active-review', 'paused'])
})

test('운영 종료는 서버가 판정한 일시 정지의 하위 묶음이다', () => {
  const ended = { ...hospital('ended', 'PAUSED'), contract_ended: true }
  const paused = { ...hospital('paused', 'PAUSED'), contract_ended: false }
  // 서버 판정이 참이어도 일시 정지가 아니면 운영 종료가 아니다(재개 직후 등).
  const resumed = { ...hospital('resumed', 'ACTIVE'), contract_ended: true }
  const rows = [ended, paused, resumed]

  assert.equal(isContractEnded(ended), true)
  assert.equal(isContractEnded(paused), false)
  assert.equal(isContractEnded(resumed), false)
  const ids = (filter: HospitalStatusFilter) =>
    rows.filter((row) => hospitalMatchesStatus(row, filter)).map((row) => row.id)
  assert.deepEqual(ids('ended'), ['ended'])
  // 운영 종료도 일시 정지 수 안에 든다 — 3상태는 그대로다.
  assert.deepEqual(ids('paused'), ['ended', 'paused'])
  assert.equal(hospitalStatusCounts(rows).ended, 1)
  assert.equal(hospitalStatusCounts(rows).paused, 2)
})
