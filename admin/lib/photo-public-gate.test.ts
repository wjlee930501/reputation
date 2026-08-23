import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import { PHOTO_PUBLIC_GATE_COPY, describePhotoPublicGate } from './photo-public-gate.ts'

const complete = { is_complete: true, missing_message: null }
const incomplete = { is_complete: false, missing_message: '사진을 공개하려면 사진 소유자을(를) 입력해야 합니다.' }

test('a pending photo with complete rights can still be published', () => {
  // 실제 게이트는 처리 상태가 아니라 권리 기록이다.
  const gate = describePhotoPublicGate({ status: 'PENDING', is_public: false, photo_provenance: complete })

  assert.equal(gate.state, 'PRIVATE_READY')
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
  assert.match(PHOTO_PUBLIC_GATE_COPY, /사용 권리 기록/)
  assert.match(PHOTO_PUBLIC_GATE_COPY, /자료 처리 상태와는 무관/)
})

test('the wiki screen states the real gate and never claims review status decides it', () => {
  const page = readFileSync(new URL('../app/hospitals/[id]/wiki/page.tsx', import.meta.url), 'utf8')

  assert.match(page, /PHOTO_PUBLIC_GATE_COPY/)
  assert.match(page, /describePhotoPublicGate/)
  assert.doesNotMatch(page, /검수 완료된 사진만/)
})
