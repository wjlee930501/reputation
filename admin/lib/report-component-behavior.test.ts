import assert from 'node:assert/strict'
import { readdirSync, readFileSync } from 'node:fs'
import test from 'node:test'

import {
  dialogKeyDecision,
  preflightDeliveryAction,
} from './report-component-behavior.ts'
import { REPORT_DIALOG_SECTION_ORDER } from './report-review.ts'

const REPORTS_DIR = new URL('../app/hospitals/[id]/reports/', import.meta.url)
const dialogSource = readFileSync(new URL('ReportDialog.tsx', REPORTS_DIR), 'utf8')
const pageSource = readFileSync(new URL('page.tsx', REPORTS_DIR), 'utf8')

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
  assert.match(pageSource, /const fresh = await loadDetail\(selected\.id\)/)
  assert.match(pageSource, /preflightDeliveryAction\(fresh, action\.kind\)/)
  assert.match(pageSource, /status === 409[\s\S]*applyReport\(await loadDetail\(selected\.id\)\)/)
  assert.match(dialogSource, /dialogKeyDecision\(/)
  assert.match(dialogSource, /previous\?\.focus\(\)/)
  assert.match(dialogSource, /issueRef\.current\?\.focus\(\)/)
})

test('the reports tab opens exactly one dialog, ordered 열기 → 전달 기록 → 이력', () => {
  // 설계 §4.3: 목록 한 줄에 ‘열기’ 하나, 그 뒤는 다이얼로그 하나뿐이다. 두 번째
  // 모달이 다시 생기면 담당자가 어느 화면에서 전달을 기록했는지 흐려진다.
  const files = readdirSync(new URL(REPORTS_DIR))
  assert.deepEqual(files.filter((name) => /Dialog|Modal/.test(name)), ['ReportDialog.tsx'])
  assert.equal(pageSource.match(/<Report(?:Dialog|ReviewDialog)\b/g)?.length, 1)
  assert.equal(pageSource.includes('createPortal'), false)

  const sections = [...dialogSource.matchAll(/data-report-section="([a-z]+)"/g)].map((match) => match[1])
  assert.deepEqual(sections, [...REPORT_DIALOG_SECTION_ORDER])
  assert.match(dialogSource, /1\. 원장용 파일 열기/)
  assert.match(dialogSource, /2\. 전달 기록/)
  assert.match(dialogSource, /3\. 전달 이력/)
  // 내부 진단은 원장 대면 구획에 섞이지 않고 접힌 ‘내부 상태’ 안에만 있다.
  assert.match(dialogSource, /<details data-report-section="internal"[\s\S]*내부 상태/)
  assert.equal(dialogSource.indexOf('data-report-section="internal"') > dialogSource.indexOf('data-report-section="history"'), true)

  for (const name of files) {
    assert.equal(readFileSync(new URL(name, REPORTS_DIR), 'utf8').includes('리포트'), false, `${name}에 통일 전 용어가 남았다`)
  }
})

test('the delivery record binds to the downloaded bytes, never to the server hash echo', () => {
  // M-08: 화면이 서버가 준 확인 번호를 그대로 되돌려 보내면 전달 기록이 무엇을
  // 증명하는지 사라진다. 담당자가 이 화면에서 실제로 내려받은 파일에만 결합한다.
  assert.match(pageSource, /artifact_sha256: action\.artifactSha256/)
  assert.equal(pageSource.includes('fresh.doctorArtifact.sha256'), false)
  assert.match(pageSource, /isArtifactMismatch\(caught\.detail\)\) setDownloadedSha256\(null\)/)

  assert.match(dialogSource, /sha256OfDownloadedPdf\(doctorUrl/)
  assert.match(dialogSource, /URL\.createObjectURL\(blob\)/)
  assert.match(dialogSource, /URL\.revokeObjectURL\(objectUrl\)/)
  assert.match(dialogSource, /disabled=\{busy \|\| !doctorUrl \|\| !deliveryValid \|\| !downloadedSha256\}/)
  assert.match(dialogSource, /먼저 원장 전달용 파일을 여기서 내려받아야 합니다/)
})
