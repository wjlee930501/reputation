import assert from 'node:assert/strict'
import test from 'node:test'

import {
  countGroup,
  countRoles,
  readSaveProblems,
  shapeIssues,
  swapRotating,
  toSavePayload,
  type MeasurementSetRow,
} from './measurement-set.ts'

function row(text: string, group: MeasurementSetRow['group'], role: MeasurementSetRow['role']): MeasurementSetRow {
  return { text, group, role }
}

function fullSet(): MeasurementSetRow[] {
  const rows: MeasurementSetRow[] = []
  for (let i = 0; i < 5; i += 1) rows.push(row(`행복의원 질문 ${i}`, 'A', 'FIXED'))
  for (let i = 0; i < 15; i += 1) rows.push(row(`강남 내과 질문 ${i}`, 'B', 'FIXED'))
  for (let i = 0; i < 10; i += 1) rows.push(row(`강남 야간 내과 ${i}`, 'D', 'ROTATING'))
  rows.push(row('강남 주차 되는 내과', 'D', null))
  return rows
}

test('30개 세트의 개수를 역할·그룹별로 센다', () => {
  const rows = fullSet()
  assert.deepEqual(countRoles(rows), { fixed: 20, rotating: 10, candidates: 1 })
  assert.deepEqual(countGroup(rows, 'D'), { fixed: 0, rotating: 10, candidates: 1 })
  assert.deepEqual(shapeIssues(rows), [])
})

test('모양이 맞지 않으면 저장 전에 알린다', () => {
  const rows = fullSet().slice(0, 12)
  assert.ok(shapeIssues(rows).some((issue) => issue.includes('고정 질문은 15~20개')))
  const brandOnly = Array.from({ length: 15 }, (_, i) => row(`행복의원 ${i}`, 'A', 'FIXED'))
  assert.ok(shapeIssues(brandOnly).some((issue) => issue.includes('B~D')))
  const duplicated = [...fullSet(), row('강남 내과 질문 0', 'B', null)]
  assert.ok(shapeIssues(duplicated).some((issue) => issue.includes('두 번')))
  const brandRotating = fullSet().map((r, i) => (i === 0 ? { ...r, role: 'ROTATING' as const } : r))
  assert.ok(shapeIssues(brandRotating).some((issue) => issue.includes('고정으로만')))
})

test('교체 질문과 후보를 맞바꿔도 교체 수는 그대로다', () => {
  const rows = fullSet()
  const rotatingIndex = rows.findIndex((r) => r.role === 'ROTATING')
  const candidateIndex = rows.findIndex((r) => r.role === null)
  const swapped = swapRotating(rows, rotatingIndex, candidateIndex)
  assert.equal(swapped[rotatingIndex].role, null)
  assert.equal(swapped[candidateIndex].role, 'ROTATING')
  assert.deepEqual(countRoles(swapped), countRoles(rows))
  // 고정 질문을 고르면 바꾸지 않는다.
  assert.equal(swapRotating(rows, 0, candidateIndex), rows)
})

test('저장 본문은 앞뒤 공백을 지운다', () => {
  assert.deepEqual(toSavePayload([row('  강남 내과 추천 ', 'B', 'FIXED')]), {
    rows: [{ text: '강남 내과 추천', group: 'B', role: 'FIXED' }],
  })
})

test('서버 422 문제를 행별·세트 문장으로 나눈다', () => {
  const { byIndex, shape } = readSaveProblems({
    code: 'MEASUREMENT_SET_INVALID',
    problems: [
      { index: 2, messages: ['기관 이름이 겹칩니다'] },
      { index: null, messages: ['fixed questions 14, requires 15..20'] },
      'bad',
    ],
  })
  assert.deepEqual(byIndex.get(2), ['기관 이름이 겹칩니다'])
  assert.deepEqual(shape, ['fixed questions 14, requires 15..20'])
  assert.deepEqual(readSaveProblems(null).shape, [])
})

test('행 수 상한과 FastAPI 검증 오류를 보인다', () => {
  const many = Array.from({ length: 61 }, (_, i) => row(`후보 ${i}`, 'B', null))
  assert.ok(shapeIssues(many).some((issue) => issue.includes('60개까지')))
  const { shape } = readSaveProblems([{ loc: ['body', 'rows'], msg: 'List should have at most 60 items' }])
  assert.deepEqual(shape, ['List should have at most 60 items'])
})
