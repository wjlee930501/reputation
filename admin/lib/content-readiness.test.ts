import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import { contentReadinessBlockers, sourceProcessingProgress } from './content-readiness.ts'

const scheduleSection = readFileSync(
  new URL('../app/hospitals/[id]/content/ScheduleSection.tsx', import.meta.url),
  'utf8',
)

test('자료가 하나도 없으면 사람이 할 일은 올리는 것뿐이다', () => {
  const blockers = contentReadinessBlockers({
    schedule_availability: {
      available: false,
      blockers: [{ code: 'sources_required', message: '병원 정보 화면에서 근거 자료를 1개 이상 올려 주세요. 처리는 자동으로 이어집니다.' }],
    },
  })
  assert.deepEqual(blockers, [
    '병원 정보 화면에서 근거 자료를 1개 이상 올려 주세요. 처리는 자동으로 이어집니다.',
  ])
})

test('남은 준비는 자동으로 진행 중이라고만 말한다 — 없는 버튼을 누르라고 하지 않는다', () => {
  const blockers = contentReadinessBlockers({
    schedule_availability: {
      available: false,
      blockers: [
        { code: 'sources_processing', message: '근거 자료 처리가 자동으로 진행 중입니다.' },
        { code: 'essence_missing', message: '콘텐츠 운영 기준을 자동으로 만드는 중입니다.' },
      ],
    },
  })

  assert.equal(blockers.length, 2)
  for (const blocker of blockers) {
    assert.doesNotMatch(blocker, /처리 시작|누르|버튼|완료해/)
  }
  assert.match(blockers[0], /자동으로 진행 중/)
  assert.match(blockers[1], /자동으로 만드는 중/)
})

test('통과한 준비와 빈 응답은 아무것도 남기지 않는다', () => {
  assert.deepEqual(contentReadinessBlockers(null), [])
  assert.deepEqual(
    contentReadinessBlockers({
      schedule_availability: { available: true, blockers: [] },
    }),
    [],
  )
})

test('서버 판정이 빠진 혼합 버전 응답은 가능으로 추정하지 않는다', () => {
  assert.deepEqual(contentReadinessBlockers({}), [
    '발행 일정 가능 여부를 서버에서 확인할 수 없습니다.',
  ])
})

test('서버가 새 blocker를 추가해도 문구를 버리지 않는다', () => {
  assert.deepEqual(
    contentReadinessBlockers({
      schedule_availability: {
        available: false,
        blockers: [{ code: 'future_rule', message: '서버가 설명한 새 차단 사유' }],
      },
    }),
    ['서버가 설명한 새 차단 사유'],
  )
})

test('발행 일정 화면은 이 문구만 쓰고 자기 문구를 새로 만들지 않는다', () => {
  assert.match(scheduleSection, /contentReadinessBlockers\(readiness\)/)
  assert.match(scheduleSection, /sourceProcessingProgress\(readiness\)/)
  // 서버가 보낸 next_action을 화면이 그대로 그리면 없는 버튼을 누르라는 지시가 나온다.
  assert.doesNotMatch(scheduleSection, /next_action/)
  assert.doesNotMatch(scheduleSection, /단계를 완료해 주세요/)
  assert.doesNotMatch(scheduleSection, /완료할 작업/)
})

test('진행 건수는 서버가 세어 줄 때만 말한다', () => {
  // 서버가 내려주는 건수는 processing_source_count 하나뿐이다.
  assert.equal(sourceProcessingProgress({ essence: { processing_source_count: 2 } }), '근거 자료 처리 중 2건')
  assert.equal(sourceProcessingProgress({ essence: { processing_source_count: 0 } }), '근거 자료 처리 중 0건')
  // 모르는 건수를 0건이라고 말하면 "아무 일도 안 하고 있다"로 읽힌다.
  assert.equal(sourceProcessingProgress({}), null)
  assert.equal(sourceProcessingProgress({ essence: {} }), null)
  assert.equal(sourceProcessingProgress(null), null)
})
