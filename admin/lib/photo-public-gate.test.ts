import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  PHOTO_PUBLIC_GATE_COPY,
  describePhotoPublicGate,
  doctorPhotoBlockerMessage,
  doctorPhotoPublicBlocker,
  type DoctorPhotoSourceLike,
} from './photo-public-gate.ts'

const complete = { is_complete: true, missing_message: null }
const incomplete = { is_complete: false, missing_message: '사진을 공개하려면 사진 소유자을(를) 입력해야 합니다.' }

test('a complete private photo is one the operator deliberately unpublished later', () => {
  // 신규 업로드는 서버가 바로 공개하므로, 이 상태는 후속 공개 클릭 대기가 아니다.
  const gate = describePhotoPublicGate({ status: 'PENDING', is_public: false, photo_provenance: complete })

  assert.equal(gate.state, 'PRIVATE_READY')
  assert.equal(gate.badge, '공개 중지됨')
  assert.equal(gate.canToggle, true)
  assert.equal(gate.reason, null)
})

test('a processed photo without rights records cannot be published', () => {
  const gate = describePhotoPublicGate({ status: 'PROCESSED', is_public: false, photo_provenance: incomplete })

  assert.equal(gate.state, 'BLOCKED_PROVENANCE')
  assert.equal(gate.canToggle, false)
  assert.equal(gate.badge, '공개 불가 · 사용 권리 미기록')
  assert.match(gate.reason ?? '', /사진 소유자/)
})

test('a photo already public can be turned back off', () => {
  const gate = describePhotoPublicGate({ status: 'PENDING', is_public: true, photo_provenance: complete })

  assert.equal(gate.state, 'PUBLIC')
  assert.equal(gate.badge, '공개 중')
  assert.equal(gate.canToggle, true)
})

test('an excluded photo says so and locks the toggle', () => {
  const gate = describePhotoPublicGate({ status: 'EXCLUDED', is_public: false, photo_provenance: complete })

  assert.equal(gate.state, 'EXCLUDED')
  assert.equal(gate.canToggle, false)
  assert.match(gate.reason ?? '', /제외를 해제/)
})

test('a missing provenance payload is treated as missing rights, not as permitted', () => {
  const gate = describePhotoPublicGate({ status: 'PENDING', is_public: false })

  assert.equal(gate.state, 'BLOCKED_PROVENANCE')
  assert.equal(gate.canToggle, false)
})

test('the gate copy names the real condition and denies the review-status one', () => {
  assert.match(PHOTO_PUBLIC_GATE_COPY, /업로드한 사진은 병원 사이트에 바로 표시/)
  assert.match(PHOTO_PUBLIC_GATE_COPY, /자료 처리 상태와는 무관/)
  assert.doesNotMatch(PHOTO_PUBLIC_GATE_COPY, /토글로 .*노출 여부를 결정/)
})

test('the photo section states the real gate and never claims review status decides it', () => {
  const section = readFileSync(
    new URL('../app/hospitals/[id]/info/PhotosSection.tsx', import.meta.url),
    'utf8',
  )

  assert.match(section, /PHOTO_PUBLIC_GATE_COPY/)
  assert.match(section, /describePhotoPublicGate/)
  assert.doesNotMatch(section, /검수 완료된 사진만/)
  assert.doesNotMatch(section, /사진 자산은 토글로/)
})

const REAL = { asset_kind: 'VERIFIED_REAL_PERSON', approved_usage: ['DOCTOR_IDENTITY'] }

function doctorPhoto(overrides: Partial<DoctorPhotoSourceLike> = {}): DoctorPhotoSourceLike {
  return {
    source_type: 'PHOTO_DOCTOR',
    status: 'PROCESSED',
    is_public: true,
    file_url: null,
    file_access_url: '/api/admin/hospitals/h/essence/sources/s/file',
    source_metadata: REAL,
    photo_provenance: { is_complete: true },
    ...overrides,
  }
}

test('공개·실제 인물·파일이 있는 원장 사진만 의료진 사진으로 나간다', () => {
  // 관리 API는 gs:// 주소를 file_url에서 숨기고 file_access_url만 준다 — 파일 있음으로 읽는다.
  assert.equal(doctorPhotoPublicBlocker(doctorPhoto()), null)
  assert.equal(
    doctorPhotoPublicBlocker(doctorPhoto({ file_url: 'https://cdn.example/doctor.jpg', file_access_url: null })),
    null,
  )
})

test('캐릭터·일러스트로 분류된 원장 사진은 공개 중이어도 나가지 않는다', () => {
  const blocker = doctorPhotoPublicBlocker(
    doctorPhoto({ source_metadata: { asset_kind: 'EDITORIAL_GRAPHIC', approved_usage: ['CONTENT_EDITORIAL'] } }),
  )
  assert.equal(blocker, 'NOT_REAL_PERSON')
  // 사이트는 다른 확인된 원장 사진으로 대신할 수 있다 — 첫 글자가 나간다고 단정하지 않는다.
  assert.match(doctorPhotoBlockerMessage(blocker!), /의료진 사진으로 나가지 않습니다/)
  assert.doesNotMatch(doctorPhotoBlockerMessage(blocker!), /첫 글자/)
  assert.match(doctorPhotoBlockerMessage(blocker!), /실제 원장 사진을 올려/)
})

test('분류가 없는 옛 원장 사진은 서버처럼 일러스트로 읽는다', () => {
  assert.equal(doctorPhotoPublicBlocker(doctorPhoto({ source_metadata: null })), 'NOT_CLASSIFIED')
  assert.equal(doctorPhotoPublicBlocker(doctorPhoto({ source_metadata: { original_filename: 'a.jpg' } as never })), 'NOT_CLASSIFIED')
})

test('실제 인물 분류여도 용도에 의료진 신원이 없으면 나가지 않는다', () => {
  assert.equal(
    doctorPhotoPublicBlocker(doctorPhoto({ source_metadata: { asset_kind: 'VERIFIED_REAL_PERSON', approved_usage: [] } })),
    'NO_DOCTOR_USAGE',
  )
  assert.doesNotMatch(doctorPhotoBlockerMessage('NO_DOCTOR_USAGE'), /일러스트/)
})

test('제외·파일 없음·권리 미기록·공개 중지는 각각의 이유로 막힌다', () => {
  assert.equal(doctorPhotoPublicBlocker(doctorPhoto({ status: 'EXCLUDED' })), 'EXCLUDED')
  assert.equal(doctorPhotoPublicBlocker(doctorPhoto({ file_url: null, file_access_url: null })), 'NO_FILE')
  assert.equal(
    doctorPhotoPublicBlocker(doctorPhoto({ is_public: false, photo_provenance: { is_complete: false } })),
    'NO_RIGHTS',
  )
  assert.equal(doctorPhotoPublicBlocker(doctorPhoto({ is_public: false })), 'NOT_PUBLIC')
})

test('원장 사진이 아닌 자료는 의료진 사진으로 나가지 않는다', () => {
  assert.equal(doctorPhotoPublicBlocker(doctorPhoto({ source_type: 'PHOTO_CLINIC_INTERIOR' })), 'NOT_REAL_PERSON')
})
