import assert from 'node:assert/strict'
import test from 'node:test'

import {
  evidenceApprovalBlockers,
  evidenceResolutionSummary,
  resolveEvidenceMap,
} from './essence-evidence.ts'

const NOTE_A = '11111111-1111-1111-1111-111111111111'
const NOTE_B = '22222222-2222-2222-2222-222222222222'

const EVIDENCE_MAP = {
  positioning: [NOTE_A, NOTE_B],
  voice: [NOTE_A],
  promise: [],
  tone: 'not-a-list',
}

function notes(...ids: string[]): Map<string, { id: string }> {
  return new Map(ids.map((id) => [id, { id }]))
}

test('resolveEvidenceMap counts every reference the panel renders', () => {
  const resolution = resolveEvidenceMap(EVIDENCE_MAP, notes(NOTE_A, NOTE_B))

  assert.equal(resolution.total, 3)
  assert.equal(resolution.resolved, 3)
  assert.deepEqual(resolution.missingIds, [])
})

test('resolveEvidenceMap reports unresolved note ids once each', () => {
  const resolution = resolveEvidenceMap(EVIDENCE_MAP, notes(NOTE_B))

  assert.equal(resolution.total, 3)
  assert.equal(resolution.resolved, 1)
  assert.deepEqual(resolution.missingIds, [NOTE_A])
})

test('resolveEvidenceMap treats a missing or empty map as nothing to check', () => {
  assert.deepEqual(resolveEvidenceMap(null, notes()), { total: 0, resolved: 0, missingIds: [] })
  assert.deepEqual(resolveEvidenceMap({}, notes()), { total: 0, resolved: 0, missingIds: [] })
})

test('approval is blocked while the supporting claims are still loading', () => {
  const blockers = evidenceApprovalBlockers({
    resolution: resolveEvidenceMap(EVIDENCE_MAP, notes()),
    loading: true,
    loadFailed: false,
  })

  assert.equal(blockers.length, 1)
  assert.match(blockers[0], /불러오는 중/)
})

test('a failed load blocks approval and points at the retry control', () => {
  // CEO 화면 리포트의 상황 — 24개 항목이 전부 해석 실패인데 승인 버튼은 살아 있었다.
  const blockers = evidenceApprovalBlockers({
    resolution: resolveEvidenceMap(EVIDENCE_MAP, notes()),
    loading: false,
    loadFailed: true,
  })

  assert.equal(blockers.length, 1)
  assert.match(blockers[0], /근거 노트 다시 불러오기/)
})

test('notes that loaded but no longer exist block approval with a different action', () => {
  const blockers = evidenceApprovalBlockers({
    resolution: resolveEvidenceMap(EVIDENCE_MAP, notes(NOTE_B)),
    loading: false,
    loadFailed: false,
  })

  assert.equal(blockers.length, 1)
  assert.match(blockers[0], /근거 노트 1개가 현재 자료에 없습니다/)
  assert.match(blockers[0], /초안을 새로 만드세요/)
})

test('approval is open once every reference resolves', () => {
  const blockers = evidenceApprovalBlockers({
    resolution: resolveEvidenceMap(EVIDENCE_MAP, notes(NOTE_A, NOTE_B)),
    loading: false,
    loadFailed: false,
  })

  assert.deepEqual(blockers, [])
})

test('a draft with no evidence references does not block approval', () => {
  const blockers = evidenceApprovalBlockers({
    resolution: resolveEvidenceMap({}, notes()),
    loading: false,
    loadFailed: false,
  })

  assert.deepEqual(blockers, [])
})

test('evidenceResolutionSummary states what the panel is actually showing', () => {
  const missing = { resolution: resolveEvidenceMap(EVIDENCE_MAP, notes(NOTE_B)), loading: false, loadFailed: false }
  assert.equal(evidenceResolutionSummary(missing), '1/3개 항목이 근거 노트와 연결됨')

  assert.match(
    evidenceResolutionSummary({ ...missing, loading: true }),
    /불러오는 중 \(3개 항목\)/,
  )
  assert.match(
    evidenceResolutionSummary({ ...missing, loadFailed: true }),
    /불러오지 못했습니다 \(3개 항목\)/,
  )
  assert.equal(
    evidenceResolutionSummary({ resolution: resolveEvidenceMap({}, notes()), loading: false, loadFailed: false }),
    '연결된 근거 노트가 없습니다.',
  )
})
