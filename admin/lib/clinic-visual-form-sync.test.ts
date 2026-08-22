// 자료 처리 폴링(5초)이 저장 전 시각 요소 입력을 지우지 않아야 한다.
//
// 온보딩 동선상 AE는 자료 처리를 돌려놓고 그 사이에 로고 URL·대표색·첫 화면 카피를
// 입력한다. 폼이 `hospital` 참조 변경마다 리셋되면 그 입력이 5초마다 사라진다.
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  clinicVisualSignature,
  clinicVisualValuesOf,
  shouldSyncFromServer,
} from './clinic-visual-form-sync.ts'

const onboardingPage = readFileSync(
  new URL('../app/hospitals/[id]/onboarding/page.tsx', import.meta.url),
  'utf8',
)

const SERVER = {
  logo_url: 'https://clinic.example/logo.png',
  brand_primary_color: '#17365D',
  hero_headline: '오늘도 문 여는 동네 주치의',
  hero_description: '증상과 진료 정보를 방문 전에 확인하세요',
  site_access_mode: 'urgent',
}

const HOSPITAL = 'hospital-1'

function syncDecision(input: {
  dirty: boolean
  syncedSignature: string
  serverSignature: string
  syncedHospitalId?: string | null
  hospitalId?: string | null
}): boolean {
  return shouldSyncFromServer({
    dirty: input.dirty,
    syncedSignature: input.syncedSignature,
    serverSignature: input.serverSignature,
    syncedHospitalId: input.syncedHospitalId ?? HOSPITAL,
    hospitalId: input.hospitalId ?? HOSPITAL,
  })
}

test('a refreshed hospital with the same values is not a reason to reset the form', () => {
  const first = clinicVisualSignature(clinicVisualValuesOf({ ...SERVER }))
  const polled = clinicVisualSignature(clinicVisualValuesOf({ ...SERVER }))

  assert.equal(first, polled)
  assert.equal(
    syncDecision({ dirty: false, syncedSignature: first, serverSignature: polled }),
    false,
  )
})

test('typing survives a poll that carries genuinely new server values', () => {
  const synced = clinicVisualSignature(clinicVisualValuesOf(SERVER))
  const changedOnServer = clinicVisualSignature(
    clinicVisualValuesOf({ ...SERVER, hero_headline: '다른 사람이 바꾼 문장' }),
  )

  assert.equal(
    syncDecision({ dirty: true, syncedSignature: synced, serverSignature: changedOnServer }),
    false,
  )
})

test('an untouched form still picks up server changes', () => {
  const synced = clinicVisualSignature(clinicVisualValuesOf(SERVER))
  const changedOnServer = clinicVisualSignature(
    clinicVisualValuesOf({ ...SERVER, logo_url: 'https://clinic.example/new.png' }),
  )

  assert.equal(
    syncDecision({ dirty: false, syncedSignature: synced, serverSignature: changedOnServer }),
    true,
  )
})

test('after a successful save the form syncs to what the server normalized', () => {
  const synced = clinicVisualSignature(clinicVisualValuesOf(SERVER))
  const afterSave = clinicVisualSignature(
    clinicVisualValuesOf({ ...SERVER, hero_description: null }),
  )

  // 저장 성공이 dirty를 풀기 때문에 다음 새로고침에서 서버 값이 반영된다.
  assert.equal(
    syncDecision({ dirty: false, syncedSignature: synced, serverSignature: afterSave }),
    true,
  )
})

test('opening another hospital discards the previous draft even mid-edit', () => {
  // 저장 전 입력을 지키는 것은 같은 병원을 다시 불러왔을 때의 이야기다. 다른 병원의
  // 화면에 이전 병원의 로고·대표색이 남으면 엉뚱한 병원에 저장된다.
  const synced = clinicVisualSignature(clinicVisualValuesOf(SERVER))
  const otherHospital = clinicVisualSignature(
    clinicVisualValuesOf({ ...SERVER, logo_url: 'https://other.example/logo.png' }),
  )

  assert.equal(
    syncDecision({
      dirty: true,
      syncedSignature: synced,
      serverSignature: otherHospital,
      syncedHospitalId: 'hospital-1',
      hospitalId: 'hospital-2',
    }),
    true,
  )
})

test('switching to a hospital whose visuals happen to match still resyncs', () => {
  const signature = clinicVisualSignature(clinicVisualValuesOf(SERVER))

  assert.equal(
    syncDecision({
      dirty: true,
      syncedSignature: signature,
      serverSignature: signature,
      syncedHospitalId: 'hospital-1',
      hospitalId: 'hospital-2',
    }),
    true,
  )
})

test('missing profile fields read as empty strings, not as unsaved edits', () => {
  const values = clinicVisualValuesOf(null)

  assert.deepEqual(values, {
    logoUrl: '',
    primaryColor: '',
    heroHeadline: '',
    heroDescription: '',
    accessMode: '',
  })
})

test('the onboarding form marks itself dirty on edit and clears it only after saving', () => {
  assert.match(onboardingPage, /function update<Field extends keyof ClinicVisualValues>/)
  assert.match(onboardingPage, /setDirty\(true\)\s*\n\s*setForm/)
  const saveStart = onboardingPage.indexOf('async function save(event: React.FormEvent)')
  const clearsDirty = onboardingPage.indexOf('setDirty(false)', saveStart)
  const feedback = onboardingPage.indexOf('공개 표면 시각 요소를 저장했습니다', saveStart)

  assert.ok(saveStart >= 0)
  assert.ok(clearsDirty > saveStart && clearsDirty < feedback)
  // 리셋 판단은 순수 함수 한 곳에서만 한다 — 폼이 참조 변경으로 초기화되지 않는다.
  assert.match(onboardingPage, /const sync = shouldSyncFromServer\(\{/)
  assert.doesNotMatch(onboardingPage, /\}, \[hospital\]\)/)
  // 병원이 바뀌면 이전 병원의 초안과 dirty 표시를 함께 버린다.
  assert.match(onboardingPage, /if \(switchedHospital\) setDirty\(false\)/)
})
