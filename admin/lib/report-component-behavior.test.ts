import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  dialogKeyDecision,
  preflightDeliveryAction,
} from './report-component-behavior.ts'

const ready = {
  deliveryReady: true,
  deliveryBlockers: [] as string[],
  doctorArtifact: { sha256: 'a'.repeat(64) },
  effectiveEventType: null,
  sentAt: null,
}

test('dialog keyboard decisions close, wrap forward, and wrap backward', () => {
  assert.equal(dialogKeyDecision('Escape', false, false, false), 'close')
  assert.equal(dialogKeyDecision('Tab', false, false, true), 'first')
  assert.equal(dialogKeyDecision('Tab', true, true, false), 'last')
  assert.equal(dialogKeyDecision('Tab', false, false, false), 'native')
})

test('fresh server state blocks stale delivery before mutation and stays actionable', () => {
  const issue = preflightDeliveryAction({
    ...ready,
    deliveryReady: false,
    deliveryBlockers: ['현재 병원 자료가 변경됐습니다.'],
  }, 'deliver')

  assert.equal(issue?.problem, '현재 병원 자료가 변경됐습니다.')
  assert.equal(issue?.action, 'operations')
  assert.match(issue?.nextAction ?? '', /운영 센터/)
})

test('fresh server state prevents a duplicate delivery but allows valid correction and rescind', () => {
  const delivered = { ...ready, effectiveEventType: 'DELIVERED' }
  assert.match(preflightDeliveryAction(delivered, 'deliver')?.title ?? '', /이미 전달/)
  assert.equal(preflightDeliveryAction(delivered, 'correct'), null)
  assert.equal(preflightDeliveryAction(delivered, 'rescind'), null)
})

test('page and dialog components wire the tested preflight and focus behavior', () => {
  const page = readFileSync(new URL('../app/hospitals/[id]/reports/page.tsx', import.meta.url), 'utf8')
  const dialog = readFileSync(new URL('../app/hospitals/[id]/reports/ReportReviewDialog.tsx', import.meta.url), 'utf8')

  assert.match(page, /const fresh = await loadDetail\(selected\.id\)/)
  assert.match(page, /preflightDeliveryAction\(fresh, action\.kind\)/)
  assert.match(page, /status === 409[\s\S]*applyReport\(await loadDetail\(selected\.id\)\)/)
  assert.match(dialog, /dialogKeyDecision\(/)
  assert.match(dialog, /previous\?\.focus\(\)/)
  assert.match(dialog, /issueRef\.current\?\.focus\(\)/)
})
