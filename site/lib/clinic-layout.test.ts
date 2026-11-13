import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildSessionGrid,
  questionSummary,
  questionText,
  resolveClinicLayout,
  seoulToday,
  selectAnswerQuestions,
  splitHoursNote,
  todayHours,
} from './clinic-layout.ts'

const CLOSED_WEEK = {
  mon: '휴진', tue: '휴진', wed: '휴진', thu: '휴진', fri: '휴진', sat: '휴진', sun: '휴진',
}

test('only A, B and C select a layout; anything else keeps the pre-layout page', () => {
  assert.equal(resolveClinicLayout('A'), 'A')
  assert.equal(resolveClinicLayout('C'), 'C')
  assert.equal(resolveClinicLayout(null), null)
  assert.equal(resolveClinicLayout(undefined), null)
  assert.equal(resolveClinicLayout('a'), null)
  assert.equal(resolveClinicLayout('D'), null)
})

test('the question list prefers question-shaped content and keeps input order within a type', () => {
  const contents = [
    { id: '1', content_type: 'LOCAL' },
    { id: '2', content_type: 'DISEASE' },
    { id: '3', content_type: 'FAQ' },
    { id: '4', content_type: 'DISEASE' },
    { id: '5', content_type: 'UNKNOWN' },
    { id: '6', content_type: 'TREATMENT' },
  ]
  assert.deepEqual(
    selectAnswerQuestions(contents, 4).map((c) => c.id),
    ['3', '2', '4', '6'],
  )
})

test('a question row uses the approved question and answer before the article title and summary', () => {
  assert.equal(questionText({ faq_question: '진료비는?', title: '글 제목' }), '진료비는?')
  assert.equal(questionText({ faq_question: null, title: '글 제목' }), '글 제목')
  assert.equal(questionSummary({ faq_answer_summary: '답변', meta_description: '요약' }), '답변')
  assert.equal(questionSummary({ faq_answer_summary: null, meta_description: '요약' }), '요약')
  assert.equal(questionSummary({ faq_answer_summary: null, meta_description: null }), null)
})

test('today is read in Korean time, not the server clock', () => {
  // 2026-11-12 23:30 UTC = 2026-11-13 08:30 KST (금)
  const today = seoulToday(new Date('2026-11-12T23:30:00Z'))
  assert.deepEqual(today, { key: 'fri', label: '금', month: 11, day: 13 })
  assert.deepEqual(todayHours({ fri: '08:00 ~ 18:00' }, 'fri'), { time: '08:00 ~ 18:00', closed: false })
  assert.deepEqual(todayHours({ sun: '휴진' }, 'sun'), { time: '휴진', closed: true })
  assert.equal(todayHours({}, 'mon'), null)
})

const JANGBOGO_HOURS = {
  mon: '08:00 ~ 18:00 (점심 13:00 ~ 14:00)',
  tue: '08:00 ~ 18:00 (점심 13:00 ~ 14:00)',
  wed: '08:00 ~ 13:00 (점심시간 없음)',
  thu: '08:00 ~ 18:00 (점심 13:00 ~ 14:00)',
  fri: '08:00 ~ 18:00 (점심 13:00 ~ 14:00)',
  sat: '08:00 ~ 13:00 (점심시간 없음)',
  sun: '휴진 (공휴일은 08:00 ~ 13:00 오전 진료)',
}

test('the session grid splits morning and afternoon at the lunch break', () => {
  const grid = buildSessionGrid(JANGBOGO_HOURS)
  assert.ok(grid)
  assert.deepEqual(
    grid.rows.map((row) => row.label),
    ['오전 08:00~13:00', '오후 14:00~18:00'],
  )
  const states = grid.rows.map((row) => row.cells.map((cell) => cell.state).join(','))
  assert.deepEqual(states, [
    'open,open,open,open,open,open,closed',
    'open,open,closed,open,open,closed,closed',
  ])
  assert.equal(grid.hasPartial, false)
})

test('a day whose hours do not match a session is marked partial, not open', () => {
  const grid = buildSessionGrid({
    ...CLOSED_WEEK,
    mon: '09:00-18:00 (점심 13:00-14:00)',
    tue: '09:00-16:00 (점심 13:00-14:00)',
  })
  assert.ok(grid)
  assert.equal(grid.rows[1].cells[0].state, 'open')
  assert.equal(grid.rows[1].cells[1].state, 'partial')
  assert.equal(grid.hasPartial, true)
})

test('a clinic without a lunch break gets one session row', () => {
  const grid = buildSessionGrid({ ...CLOSED_WEEK, mon: '09:00-21:00', sat: '09:00-13:00' })
  assert.ok(grid)
  assert.deepEqual(grid.rows.map((row) => row.label), ['진료 09:00~21:00'])
  assert.equal(grid.rows[0].cells[0].state, 'open')
  assert.equal(grid.rows[0].cells[5].state, 'partial')
})

test('the grid is not invented when any day cannot be read', () => {
  assert.equal(buildSessionGrid({ mon: '오전 진료', tue: '09:00-18:00' }), null)
  assert.equal(buildSessionGrid({ mon: '09:00-12:00, 14:00-18:00' }), null)
  assert.equal(buildSessionGrid({}), null)
  assert.equal(buildSessionGrid(null), null)
  assert.equal(buildSessionGrid({ sun: '휴진' }), null)
})

test('a day left blank is not shown as closed — the grid falls back to the list', () => {
  // 월요일만 적고 나머지를 비우면 6일을 '진료 없음'으로 단정하게 된다.
  assert.equal(buildSessionGrid({ mon: '09:00-18:00' }), null)
  assert.equal(buildSessionGrid({ ...CLOSED_WEEK, mon: '09:00-18:00', tue: ' ' }), null)
})

test('impossible times and a lunch break outside the day are not drawn', () => {
  assert.equal(buildSessionGrid({ ...CLOSED_WEEK, mon: '29:00-30:00' }), null)
  assert.equal(buildSessionGrid({ ...CLOSED_WEEK, mon: '09:75-18:00' }), null)
  assert.equal(buildSessionGrid({ ...CLOSED_WEEK, mon: '09:00-18:00 (점심 14:00-13:00)' }), null)
  assert.equal(buildSessionGrid({ ...CLOSED_WEEK, mon: '09:00-18:00 (점심 08:00-09:30)' }), null)
  assert.equal(buildSessionGrid({ ...CLOSED_WEEK, mon: '09:00-18:00 (점심 25:00)' }), null)
  assert.equal(buildSessionGrid({ ...CLOSED_WEEK, mon: '09:00-18:00 (점심 13:00)' }), null)
  assert.ok(buildSessionGrid({ ...CLOSED_WEEK, mon: '09:00-13:00 (점심시간 없음)' }))
  // 점심 구절 밖의 시각(접수 마감)은 점심시간이 아니다.
  const withNote = buildSessionGrid({ ...CLOSED_WEEK, mon: '08:30 ~ 18:30 (점심 13:00 ~ 14:00, 접수 마감 18:00)' })
  assert.ok(withNote)
  assert.deepEqual(withNote.rows.map((row) => row.label), ['오전 08:30~13:00', '오후 14:00~18:30'])
  assert.ok(buildSessionGrid({ ...CLOSED_WEEK, mon: '09:00-24:00' }))
})

test('splitHoursNote moves only a trailing parenthesis to its own line', () => {
  assert.deepEqual(splitHoursNote('08:00 ~ 18:30 (점심 12:30 ~ 14:00)'), {
    main: '08:00 ~ 18:30',
    note: '(점심 12:30 ~ 14:00)',
  })
  assert.deepEqual(splitHoursNote('08:00 ~ 13:00'), { main: '08:00 ~ 13:00', note: null })
  assert.deepEqual(splitHoursNote('휴진 (공휴일은 08:00 ~ 13:00 오전 진료)'), {
    main: '휴진',
    note: '(공휴일은 08:00 ~ 13:00 오전 진료)',
  })
})
