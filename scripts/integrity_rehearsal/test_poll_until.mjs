import assert from 'node:assert/strict'
import { pollUntil } from './poll_until.mjs'

const noSleep = async () => {}

// 워커는 실행을 SUCCEEDED로 커밋한 뒤 별도 세션에서 사고를 새 실행으로 옮긴다
// (tasks.regenerate_content_item → recover_generation_incidents). 그 사이에 읽은 사고는
// 아직 옛 실행을 가리킨다 — 한 번 읽고 끝내면 정상 흐름이 실패로 보인다.
const reads = [{ runId: 'old' }, { runId: 'old' }, { runId: 'replacement' }]
let calls = 0
const settled = await pollUntil(
  async () => reads[Math.min(calls++, reads.length - 1)],
  (value) => value.runId === 'replacement',
  { timeoutMs: 1_000, intervalMs: 1, sleep: noSleep },
)
assert.equal(settled.runId, 'replacement')
assert.equal(calls, 3)

// 기한 안에 끝내 맞지 않으면 마지막 관측을 그대로 돌려준다 — 호출자가 그 값으로 실패를 말한다.
let now = 0
const last = await pollUntil(
  async () => ({ runId: 'old', at: now }),
  (value) => value.runId === 'replacement',
  { timeoutMs: 50, intervalMs: 10, sleep: async (ms) => { now += ms }, clock: () => now },
)
assert.equal(last.runId, 'old')
assert(last.at >= 50)

console.log(JSON.stringify({ status: 'passed', settlesAfterLag: true, returnsLastObservationOnTimeout: true }))
