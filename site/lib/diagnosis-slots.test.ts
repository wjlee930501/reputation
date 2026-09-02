import assert from 'node:assert/strict'
import test from 'node:test'

import { DIAGNOSIS_SLOT_RESET_HOUR_KST, diagnosisSlotResetCopy, parseDiagnosisSlots } from './diagnosis-slots.ts'

test('slot counter keeps the server counts used by the numeric quota', () => {
  assert.deepEqual(
    parseDiagnosisSlots({ total: 20, used: 7, remaining: 13 }),
    { total: 20, used: 7, remaining: 13, soldOut: false },
  )
})

test('slot counter rejects malformed or internally inconsistent availability', () => {
  assert.equal(parseDiagnosisSlots({ total: 20, used: 7, remaining: 12 }), null)
  assert.equal(parseDiagnosisSlots({ total: 20, used: -1, remaining: 21 }), null)
  assert.equal(parseDiagnosisSlots(null), null)
})

test('slot counter reports a sold-out day', () => {
  assert.deepEqual(
    parseDiagnosisSlots({ total: 20, used: 20, remaining: 0 }),
    { total: 20, used: 20, remaining: 0, soldOut: true },
  )
})

test('slot reset copy renders the configured hour, never a hardcoded one', () => {
  // 이 파일은 **문구 렌더링만** 검증한다. 상수 자체가 백엔드
  // SLOT_RESET_HOUR_KST(app/api/public/diagnosis.py)와 같은지는 여기서 리터럴과
  // 비교해 봐야 아무것도 못 잡는다 — 값을 바꾸는 사람이 같은 파일의 리터럴도 같이
  // 고치면 계속 초록이기 때문이다. 두 소스 대조는 양쪽 소스 텍스트를 읽는
  // scripts/test_copy_contracts.py가 CI에서 맡는다.
  assert.equal(diagnosisSlotResetCopy(DIAGNOSIS_SLOT_RESET_HOUR_KST), `매일 오전 ${DIAGNOSIS_SLOT_RESET_HOUR_KST}시에 새 접수가 열립니다.`)
  assert.equal(diagnosisSlotResetCopy(), diagnosisSlotResetCopy(DIAGNOSIS_SLOT_RESET_HOUR_KST))
  assert.equal(diagnosisSlotResetCopy(9), '매일 오전 9시에 새 접수가 열립니다.')
})
