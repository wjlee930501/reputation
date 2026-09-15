import assert from 'node:assert/strict'
import test from 'node:test'

import {
  doctorPhotoOptions,
  emptyPhysician,
  joinLines,
  movePhysician,
  parseCommaList,
  parseLines,
  physicianUsingPhoto,
  physiciansPayload,
  removePhysician,
  seedPhysicians,
  withDisplayOrder,
} from './physicians.ts'
import type { Physician } from '../types/index.ts'

function row(name: string, order: number): Physician {
  return { ...emptyPhysician(order), name }
}

test('새 줄은 비어 있고 대표도 아니다', () => {
  assert.deepEqual(emptyPhysician(2), {
    name: '',
    title: '',
    specialties: [],
    career: '',
    credentials: null,
    photo_source_id: null,
    display_order: 2,
    is_representative: false,
  })
})

test('표시 순서는 화면의 줄 순서와 같다', () => {
  const rows = [row('가', 5), row('나', 0)]
  assert.deepEqual(withDisplayOrder(rows).map((r) => [r.name, r.display_order]), [['가', 0], ['나', 1]])
})

test('줄을 옮기면 순서 번호도 함께 다시 매긴다', () => {
  const rows = withDisplayOrder([row('가', 0), row('나', 1), row('다', 2)])
  const moved = movePhysician(rows, 2, -1)
  assert.deepEqual(moved.map((r) => r.name), ['가', '다', '나'])
  assert.deepEqual(moved.map((r) => r.display_order), [0, 1, 2])
  // 끝에서는 아무 일도 하지 않는다.
  assert.equal(movePhysician(rows, 0, -1), rows)
  assert.equal(movePhysician(rows, 2, 1), rows)
})

test('줄을 지워도 남은 줄의 순서가 이어진다', () => {
  const rows = withDisplayOrder([row('가', 0), row('나', 1), row('다', 2)])
  const next = removePhysician(rows, 1)
  assert.deepEqual(next.map((r) => [r.name, r.display_order]), [['가', 0], ['다', 1]])
  // 원본은 그대로 — 화면 상태를 제자리에서 바꾸지 않는다.
  assert.equal(rows.length, 3)
})

test('줄 단위·쉼표 입력은 빈 값을 버린다', () => {
  assert.deepEqual(parseLines(' 외과전문의 \n\n 대장항문외과 '), ['외과전문의', '대장항문외과'])
  assert.deepEqual(parseCommaList('외과, , 대장항문외과 '), ['외과', '대장항문외과'])
  assert.equal(joinLines(['가', '나']), '가\n나')
  assert.equal(joinLines(null), '')
})

test('이름이 빈 줄은 저장하지 않는다 — 전체 교체가 이름 없는 의료진을 만들지 않게', () => {
  const payload = physiciansPayload([row(' 김민수 ', 0), emptyPhysician(1), row('이서연', 2)])
  assert.deepEqual(payload.map((r) => [r.name, r.display_order]), [['김민수', 0], ['이서연', 1]])
})

test('한 명도 없는 병원은 빈 줄 하나로 시작한다', () => {
  assert.deepEqual(seedPhysicians([]).map((r) => r.name), [''])
  assert.deepEqual(seedPhysicians(null).length, 1)
  const seeded = seedPhysicians([row('나', 3), row('가', 1)])
  assert.deepEqual(seeded.map((r) => [r.name, r.display_order]), [['가', 0], ['나', 1]])
})

test('사진 선택지는 이 병원의 제외되지 않은 원장 사진뿐이다', () => {
  assert.deepEqual(
    doctorPhotoOptions([
      { id: 'p1', source_type: 'PHOTO_DOCTOR', title: '원장 프로필', status: 'PROCESSED' },
      { id: 'p2', source_type: 'PHOTO_DOCTOR', title: '옛 사진', status: 'EXCLUDED' },
      { id: 'p3', source_type: 'PHOTO_CLINIC_INTERIOR', title: '대기실', status: 'PROCESSED' },
      { id: 'p4', source_type: 'HOMEPAGE', title: '홈페이지', status: 'PROCESSED' },
    ]),
    [{ id: 'p1', title: '원장 프로필' }],
  )
})

test('사진을 쓰는 의료진을 사진 화면이 찾아 쓴다', () => {
  const rows = [{ ...row('김민수', 0), photo_source_id: 'p1' }, row('이서연', 1)]
  assert.equal(physicianUsingPhoto(rows, 'p1')?.name, '김민수')
  assert.equal(physicianUsingPhoto(rows, 'p9'), null)
})
