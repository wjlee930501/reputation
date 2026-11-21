import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import { FACTS_PATCH_FIELDS } from './profile-patch.ts'
import {
  FACT_FIELD_SECTIONS,
  changedFactFields,
  changedSections,
  unsavedSummary,
} from './unsaved-changes.ts'

const saved = {
  address: '경산시 압량읍 1',
  phone: '053-000-0000',
  region: ['경산'],
  business_hours: { mon: '09:00~18:00' },
  director_philosophy: null,
  physicians: [{ name: '김원장', is_representative: true }],
  hero_headline: '브랜드 칸',
}

test('바뀐 칸만 세고, 빈 값끼리와 키 순서는 같다고 본다', () => {
  assert.deepEqual(changedFactFields(saved, { ...saved }), [])
  assert.deepEqual(
    changedFactFields(saved, { ...saved, director_philosophy: '', region: ['경산'], business_hours: { mon: '09:00~18:00', tue: '' } }),
    [],
  )
  assert.deepEqual(changedFactFields(saved, { ...saved, phone: '053-111-1111' }), ['phone'])
  // 사람이 비운 칸도 변경이다.
  assert.deepEqual(changedFactFields(saved, { ...saved, address: '' }), ['address'])
})

test('탭 밖 칸(브랜드)은 세지 않는다 — 그 칸의 버튼이 저장한다', () => {
  assert.deepEqual(changedFactFields(saved, { ...saved, hero_headline: '다른 문구' }), [])
})

test('의료진은 양쪽 다 읽혔을 때만 비교한다', () => {
  const { physicians: _omit, ...withoutPhysicians } = saved
  void _omit
  assert.deepEqual(changedFactFields(withoutPhysicians, { ...saved }), [])
  assert.deepEqual(
    changedFactFields(saved, { ...saved, physicians: [{ name: '이원장', is_representative: true }] }),
    ['physicians'],
  )
})

test('모든 사실 PATCH 칸이 구획에 묶여 있다', () => {
  for (const field of FACTS_PATCH_FIELDS) {
    assert.ok(field in FACT_FIELD_SECTIONS, `${field}가 구획에 없다`)
  }
})

test('바 문구는 바뀐 구획을 화면 순서로 말한다', () => {
  assert.equal(unsavedSummary([]), '변경 없음')
  const sections = changedSections(['competitors', 'phone', 'physicians'])
  assert.deepEqual(sections, ['director', 'contact', 'targeting'])
  assert.equal(unsavedSummary(sections), '저장 안 된 변경 3곳 · 병원·원장, 연락처·진료시간, 운영 기준 정보')
})

test('병원 정보 화면은 저장 바 하나로 저장하고 떠날 때 묻는다', () => {
  const page = readFileSync(new URL('../app/hospitals/[id]/info/page.tsx', import.meta.url), 'utf8')
  assert.equal(page.match(/type="submit"/g)?.length, 1, '저장 버튼은 저장 바 하나뿐이다')
  assert.match(page, /data-testid="info-save-bar"/)
  assert.match(page, /useLeaveGuard\(changedFields\.length > 0 && !saving/)
  // 보내는 본문은 그대로 사실 칸 허용 목록이다 — 다른 섹션 값을 덮지 않는 보호를 지킨다.
  assert.match(page, /factsPatchPayload\(profile\)/)
  assert.match(page, /changedFieldSet\.has\(fieldKey\)\) return 'border-amber-400/)
})
