// 운영 기준 화면이 근거 노트를 실제로 읽어오고, 못 읽었을 때 승인을 막는지.
//
// 목록 API(GET /essence/sources)는 evidence_notes를 항상 null로 준다. 초안의
// evidence_map은 노트 UUID만 담으므로, 자료 상세를 따로 읽지 않으면 오른쪽 패널의
// 모든 항목이 해석 실패로 보이면서 승인 버튼만 멀쩡히 살아 있게 된다.
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const page = readFileSync(
  new URL('../app/hospitals/[id]/essence/page.tsx', import.meta.url),
  'utf8',
)

test('the page fetches source details for every source that carries extracted claims', () => {
  assert.match(page, /fetchAPI<SourceAsset>\(`\/admin\/hospitals\/\$\{id\}\/essence\/sources\/\$\{sourceId\}`\)/)
  assert.match(page, /\(source\.evidence_note_count \?\? 0\) > 0/)
  assert.match(page, /await loadEvidenceNotes\(/)
})

test('a failed detail fetch is remembered per source so retry can target it', () => {
  assert.match(page, /setEvidenceFailedSourceIds\(/)
  assert.match(page, /results\.filter\(\(result\) => result\.notes === null\)/)
})

test('a reload replaces the notes of the sources it read, keyed by source', () => {
  // id를 키로 병합하면 자료 재처리로 사라진 노트가 남아 죽은 참조가 해석에 성공한
  // 것처럼 보이고, 승인 잠금이 막아야 할 상황에서 풀린다.
  assert.match(page, /useState<Map<string, EvidenceNote\[\]>>/)
  assert.match(page, /setEvidenceNotesBySource\(\(prev\) => replaceSourceNotes\(/)
})

test('a full refresh rebuilds the store so removed sources leave no notes behind', () => {
  // 제외된 자료의 노트가 남으면, 서버가 그 초안의 근거로 인정하지 않는 노트를 화면만
  // 해석에 성공한 것으로 보여준다.
  assert.match(page, /replaceSourceNotes\(replaceAll \? new Map\(\) : prev, results\)/)
  assert.match(page, /\{ replaceAll: true \},/)
  // 재시도는 실패한 자료만 다루므로 나머지 보관함을 유지한다.
  assert.match(page, /return loadEvidenceNotes\(retryIds\)/)
})

test('the per-source store is the only source of notes on this screen', () => {
  // 목록·상세 응답의 노트를 덧씌우거나 그대로 그리면, 다시 불러오기로 지운 옛 노트가
  // 그 경로로 되살아난다.
  assert.match(page, /indexNotesById\(evidenceNotesBySource\)/)
  assert.match(page, /evidenceNotesBySource\.get\(selectedSource\.id\) \?\? \[\]/)
  assert.match(page, /<EvidenceList notes=\{selectedSourceNotes\} \/>/)
  assert.doesNotMatch(page, /selectedSource\?\.evidence_notes/)
  assert.doesNotMatch(page, /notes=\{selectedSource\.evidence_notes/)
})

test('a directly fetched source detail is written back into the store', () => {
  // 상세를 화면 상태로만 들고 있으면 근거 패널과 자료 패널이 다른 노트를 보게 된다.
  assert.match(page, /const rememberSourceDetail = useCallback\(\(detail: SourceAsset\) => \{/)
  assert.match(page, /replaceSourceNotes\(prev, \[\{ sourceId: detail\.id, notes: detail\.evidence_notes \?\? \[\] \}\]\)/)
  const openSource = page.slice(page.indexOf('async function openSource('))
  assert.match(openSource.slice(0, 500), /rememberSourceDetail\(detail\)/)
  const processSource = page.slice(page.indexOf('async function processSource('))
  assert.match(processSource.slice(0, 700), /rememberSourceDetail\(detail\)/)
})

test('the evidence panel exposes a retry control', () => {
  assert.match(page, /onClick=\{retryEvidenceNotes\}/)
  assert.match(page, /근거 노트 다시 불러오기/)
})

test('approval is gated on the same blockers the panel shows', () => {
  const approveButton = page.slice(page.indexOf('onClick={approveDraft}'))
  assert.match(approveButton, /evidenceBlockers\.length > 0 \|\|/)
  // 체크박스도 함께 잠근다 — 확인할 수 없는 것을 확인했다고 체크하게 두면 안 된다.
  assert.match(page, /disabled=\{evidenceBlockers\.length > 0\}/)
  assert.match(page, /evidenceBlockers\.map\(\(reason\) => \(/)
})
