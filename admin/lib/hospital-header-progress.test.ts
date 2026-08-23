import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import { summarizeHeaderProgress } from './hospital-header-progress.ts'

const layout = readFileSync(
  new URL('../app/hospitals/[id]/layout.tsx', import.meta.url),
  'utf8',
)

test('the collapsed line names what is still missing, so folding loses nothing', () => {
  const summary = summarizeHeaderProgress({
    profile_complete: true,
    v0_report_done: true,
    site_built: false,
    schedule_set: false,
    site_live: false,
  })

  assert.equal(summary.doneCount, 2)
  assert.equal(summary.total, 5)
  assert.deepEqual(summary.pendingLabels, ['콘텐츠 허브 준비', '스케줄 설정', '병원 정보 허브'])
  assert.equal(summary.label, '운영 준비 2/5 · 남은 항목 콘텐츠 허브 준비, 스케줄 설정, 병원 정보 허브')
})

test('a fully ready hospital says so without listing an empty remainder', () => {
  const summary = summarizeHeaderProgress({
    profile_complete: true,
    v0_report_done: true,
    site_built: true,
    schedule_set: true,
    site_live: true,
  })

  assert.equal(summary.label, '운영 준비 5/5 완료')
  assert.deepEqual(summary.pendingLabels, [])
})

test('a missing hospital counts nothing as done rather than guessing', () => {
  const summary = summarizeHeaderProgress(null)

  assert.equal(summary.doneCount, 0)
  assert.equal(summary.items.every((item) => item.done === false), true)
})

test('the five progress items keep their order and their names', () => {
  assert.deepEqual(
    summarizeHeaderProgress(null).items.map((item) => item.label),
    ['필수 병원 정보', '초기 진단 리포트', '콘텐츠 허브 준비', '스케줄 설정', '병원 정보 허브'],
  )
})

test('the header folds the progress row at lg and only expands it at xl', () => {
  assert.match(layout, /summarizeHeaderProgress/)
  // 접힌 한 줄은 lg에서만, 펼친 다섯 칩은 xl 이상에서만 보인다.
  assert.match(layout, /lg:inline-flex xl:hidden/)
  assert.match(layout, /hidden max-w-xl flex-wrap items-center gap-x-3 gap-y-2 [^"]*xl:flex/)
})

test('a long public address truncates instead of widening the header', () => {
  // 자기 도메인이든 기본 플랫폼 주소든 헤더 폭을 밀어내면 안 된다. 주소 표시는
  // readHospitalDomainStatus의 detail 하나로 통일돼 있으므로(O-7) 그 자리를 본다.
  assert.match(layout, /truncate[\s\S]{0,400}?readHospitalDomainStatus\(hospital\)\.detail/)
})

test('the header names the platform address instead of calling a live site "준비 중"', () => {
  // 자기 도메인이 없다는 것과 공개 주소가 없다는 것은 다르다 — 기본 주소로 이미
  // 서비스 중인 병원을 "준비 중"이라 부르면 살아 있는 주소를 없는 것으로 읽는다(O-7).
  assert.doesNotMatch(layout, /공개 주소 준비 중/)
  assert.match(layout, /readHospitalDomainStatus/)
})
