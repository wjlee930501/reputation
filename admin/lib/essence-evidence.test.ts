import assert from 'node:assert/strict'
import test from 'node:test'

import {
  evidenceApprovalBlockers,
  evidenceResolutionSummary,
  indexNotesById,
  replaceSourceNotes,
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

// ── 다시 불러오기는 병합이 아니라 교체다 ──────────────────────────────────────

const SOURCE_A = 'source-a'
const SOURCE_B = 'source-b'

test('reloading a source replaces its notes instead of keeping the old ones', () => {
  // 자료를 다시 처리하면 옛 노트는 삭제되고 새 id가 생긴다. 병합하면 사라진 노트가
  // 화면에 남아, 초안이 참조하는 죽은 id가 해석에 성공한 것처럼 보인다.
  const before = new Map([[SOURCE_A, [{ id: NOTE_A }]]])

  const after = replaceSourceNotes(before, [{ sourceId: SOURCE_A, notes: [{ id: NOTE_B }] }])

  assert.deepEqual(after.get(SOURCE_A), [{ id: NOTE_B }])
  assert.equal(indexNotesById(after).has(NOTE_A), false)
  assert.equal(indexNotesById(after).has(NOTE_B), true)
})

test('a source that failed to reload is dropped rather than left with stale notes', () => {
  const before = new Map([[SOURCE_A, [{ id: NOTE_A }]]])

  const after = replaceSourceNotes(before, [{ sourceId: SOURCE_A, notes: null }])

  assert.equal(after.has(SOURCE_A), false)
  // 그 결과 초안 참조가 미해석으로 남아 승인이 잠긴다.
  const resolution = resolveEvidenceMap({ positioning: [NOTE_A] }, indexNotesById(after))
  assert.deepEqual(resolution.missingIds, [NOTE_A])
})

test('reloading one source leaves the other sources untouched', () => {
  const before = new Map([
    [SOURCE_A, [{ id: NOTE_A }]],
    [SOURCE_B, [{ id: NOTE_B }]],
  ])

  const after = replaceSourceNotes(before, [{ sourceId: SOURCE_A, notes: [] }])

  assert.deepEqual(after.get(SOURCE_A), [])
  assert.deepEqual(after.get(SOURCE_B), [{ id: NOTE_B }])
})

test('indexNotesById lets a freshly fetched source detail win over the stored copy', () => {
  const stored = new Map([[SOURCE_A, [{ id: NOTE_A, claim: '옛 문구' }]]])

  const index = indexNotesById(stored, [{ id: NOTE_A, claim: '새 문구' }])

  assert.equal(index.get(NOTE_A)?.claim, '새 문구')
})

test('indexNotesById tolerates a missing overlay', () => {
  const stored = new Map([[SOURCE_A, [{ id: NOTE_A }]]])

  assert.equal(indexNotesById(stored, null).size, 1)
  assert.equal(indexNotesById(stored, undefined).size, 1)
  assert.equal(indexNotesById(new Map()).size, 0)
})
