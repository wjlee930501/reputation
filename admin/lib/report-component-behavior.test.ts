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
  assert.match(preflightDeliveryAction(delivered, 'deliver')?.title ?? '', /이미 고객 전달/)
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

test('the delivery record binds to the downloaded bytes, never to the server hash echo', () => {
  // M-08: 화면이 서버가 준 확인 번호를 그대로 되돌려 보내면 전달 기록이 무엇을
  // 증명하는지 사라진다. 담당자가 이 화면에서 실제로 내려받은 파일에만 결합한다.
  const page = readFileSync(new URL('../app/hospitals/[id]/reports/page.tsx', import.meta.url), 'utf8')
  const delivery = readFileSync(new URL('../app/hospitals/[id]/reports/ReportDelivery.tsx', import.meta.url), 'utf8')

  assert.match(page, /artifact_sha256: action\.artifactSha256/)
  assert.equal(page.includes('fresh.doctorArtifact.sha256'), false)
  assert.match(page, /isArtifactMismatch\(caught\.detail\)\) setDownloadedSha256\(null\)/)

  assert.match(delivery, /sha256OfDownloadedPdf\(doctorUrl/)
  assert.match(delivery, /URL\.createObjectURL\(blob\)/)
  assert.match(delivery, /URL\.revokeObjectURL\(objectUrl\)/)
  assert.match(delivery, /disabled=\{busy \|\| !doctorUrl \|\| !deliveryValid \|\| !downloadedSha256\}/)
  assert.match(delivery, /먼저 원장 전달용 파일을 여기서 내려받아야 합니다/)
})
