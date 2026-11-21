// 섹션 설정 줄바꿈(hero.line_breaks) — 원문 한 벌에 기기별 <br> 자리. 규칙은 백엔드 site_config_validation.lines_match와 같다.
import assert from 'node:assert/strict'
import test from 'node:test'

import { joinedText, lineBreakMap, lineBreakOffsets, lineBreakPieces, partStarts } from './clinic-line-breaks.ts'

const SOURCE = '365서귀포삼성정형외과 제주 서귀포 정형외과 전문의 진료'

test('a break at a space sits after the space; a break inside a word sits there', () => {
  assert.deepEqual(lineBreakOffsets(SOURCE, '365서귀포삼성정형외과\n제주 서귀포 정형외과 전문의 진료'), ['365서귀포삼성정형외과 '.length])
  assert.deepEqual(lineBreakOffsets(SOURCE, '365서귀포\n삼성정형외과 제주 서귀포 정형외과 전문의 진료'), ['365서귀포'.length])
})

test('lines that do not join back to the source are rejected (like the backend)', () => {
  assert.equal(lineBreakOffsets(SOURCE, '365서귀포삼성정형외과\n제주 정형외과'), null)
  assert.equal(lineBreakOffsets(SOURCE, '365서귀포삼성정형외과제주\n서귀포 정형외과 전문의 진료'), null)
  assert.equal(lineBreakOffsets(SOURCE, '365서귀포삼성정형외과\n\n제주 서귀포 정형외과 전문의 진료'), null)
  assert.deepEqual(lineBreakOffsets(SOURCE, SOURCE), [])
})

test('the map merges pc and mobile breaks and drops a device that does not match', () => {
  const map = lineBreakMap(SOURCE, {
    pc: '365서귀포삼성정형외과\n제주 서귀포 정형외과 전문의 진료',
    mobile: '365서귀포삼성정형외과\n제주 서귀포\n정형외과 전문의 진료',
  })
  const first = '365서귀포삼성정형외과 '.length
  const second = '365서귀포삼성정형외과 제주 서귀포 '.length
  assert.deepEqual([...map.entries()], [[first, 'both'], [second, 'mobile']])
  assert.equal(lineBreakMap(SOURCE, { pc: '다른 글', mobile: null }).size, 0)
  assert.equal(lineBreakMap(null, { pc: SOURCE }).size, 0)
})

test('pieces keep the text once and put device breaks between', () => {
  const map = lineBreakMap(SOURCE, { mobile: '365서귀포삼성정형외과\n제주 서귀포 정형외과 전문의 진료' })
  const pieces = lineBreakPieces(joinedText(SOURCE), 0, map)
  assert.deepEqual(pieces, [
    { kind: 'text', text: '365서귀포삼성정형외과 ' },
    { kind: 'br', device: 'mobile' },
    { kind: 'text', text: '제주 서귀포 정형외과 전문의 진료' },
  ])
  assert.equal(
    pieces.map((piece) => (piece.kind === 'text' ? piece.text : '')).join(''),
    joinedText(SOURCE),
  )
})

test('a part that starts later in the joined text gets only its own breaks; a break at its start is skipped', () => {
  const map = lineBreakMap('앞 조각 강조 조각입니다', { pc: '앞 조각\n강조\n조각입니다' })
  // 강조 조각은 이은 글의 5번째 글자('강')부터다 — 그 시작 자리의 줄바꿈은 조각 경계(이미 다른 줄)라 넣지 않는다.
  assert.deepEqual(lineBreakPieces('강조 조각입니다', 5, map), [
    { kind: 'text', text: '강조 ' },
    { kind: 'br', device: 'pc' },
    { kind: 'text', text: '조각입니다' },
  ])
})

test('a source with newlines or repeated spaces is cut on its joined form', () => {
  const raw = '토요일에도  진료합니다.\n평일은 저녁 7시까지입니다.'
  const map = lineBreakMap(raw, { pc: '토요일에도 진료합니다.\n평일은 저녁 7시까지입니다.' })
  assert.deepEqual(lineBreakPieces(raw, 0, map), [
    { kind: 'text', text: '토요일에도 진료합니다. ' },
    { kind: 'br', device: 'pc' },
    { kind: 'text', text: '평일은 저녁 7시까지입니다.' },
  ])
})

test('a trailing line separator and Python-only spaces (NEL, \\x1c) match like the backend', () => {
  assert.deepEqual(lineBreakOffsets('앞 문장 뒷문장', '앞 문장\n뒷문장\n'), ['앞 문장 '.length])
  assert.deepEqual(lineBreakOffsets('앞 문장 뒷문장', '앞 문장 뒷문장 '), ['앞 문장 '.length])
  assert.equal(lineBreakOffsets('앞 문장 뒷문장', '앞 문장\n뒷문장\n\n'), null)
  assert.deepEqual(lineBreakOffsets('앞 문장\u0085뒷문장', '앞 문장\n뒷문장'), ['앞 문장 '.length])
  assert.equal(joinedText('앞\x1c뒤﻿'), '앞 뒤﻿')
})

test('a part that continues the previous line draws the break at its start', () => {
  const map = lineBreakMap('앞 줄 가운데 줄 강조 줄', { pc: '앞 줄\n가운데 줄 강조 줄' })
  assert.deepEqual(lineBreakPieces('가운데 줄', 4, map, true), [
    { kind: 'br', device: 'pc' },
    { kind: 'text', text: '가운데 줄' },
  ])
  assert.deepEqual(lineBreakPieces('가운데 줄', 4, map), [{ kind: 'text', text: '가운데 줄' }])
})

test('a part that joins to nothing takes no place in the joined text', () => {
  assert.deepEqual(partStarts(['앞', '\u0085', '가나다라']), [
    { text: '앞', start: 0 },
    { text: '', start: 2 },
    { text: '가나다라', start: 2 },
  ])
  const map = lineBreakMap('앞\n\u0085\n가나다라', { pc: '앞 가나다\n라' })
  assert.deepEqual(lineBreakPieces('가나다라', 2, map), [
    { kind: 'text', text: '가나다' },
    { kind: 'br', device: 'pc' },
    { kind: 'text', text: '라' },
  ])
})
