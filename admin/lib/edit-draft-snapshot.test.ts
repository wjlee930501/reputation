import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildDraftSnapshotKey,
  clearDraftSnapshot,
  draftDiffersFromCurrent,
  editFieldsDiffer,
  parseDraftSnapshot,
  readDraftSnapshot,
  saveDraftSnapshot,
  serializeDraftSnapshot,
} from './edit-draft-snapshot.ts'

test('buildDraftSnapshotKey scopes the storage key by hospital and content id', () => {
  assert.equal(buildDraftSnapshotKey('h-1', 'c-1'), 'reputation.contentDraft.h-1.c-1')
  assert.notEqual(buildDraftSnapshotKey('h-1', 'c-1'), buildDraftSnapshotKey('h-2', 'c-1'))
})

test('serializeDraftSnapshot round-trips through parseDraftSnapshot', () => {
  const raw = serializeDraftSnapshot(
    { title: '제목', body: '본문', meta_description: '설명', references: [{ title: '학회', url: 'https://a.example' }] },
    1_700_000_000_000,
  )
  const parsed = parseDraftSnapshot(raw)

  assert.deepEqual(parsed, {
    title: '제목',
    body: '본문',
    meta_description: '설명',
    references: [{ title: '학회', url: 'https://a.example' }],
    savedAt: 1_700_000_000_000,
  })
})

test('parseDraftSnapshot rejects null, malformed JSON, and missing/invalid fields', () => {
  assert.equal(parseDraftSnapshot(null), null)
  assert.equal(parseDraftSnapshot('not json'), null)
  assert.equal(parseDraftSnapshot('{"title": "제목"}'), null)
  assert.equal(parseDraftSnapshot(JSON.stringify({ title: '제목', body: '본문', meta_description: '설명', references: 'oops', savedAt: 1 })), null)
})

test('draftDiffersFromCurrent flags a change only when title/body/meta differ', () => {
  const draft = { title: 'A', body: 'B', meta_description: 'C', references: [], savedAt: 1 }
  assert.equal(draftDiffersFromCurrent(draft, { title: 'A', body: 'B', meta_description: 'C' }), false)
  assert.equal(draftDiffersFromCurrent(draft, { title: 'A', body: '다른 본문', meta_description: 'C' }), true)
})

test('editFieldsDiffer is false when the edit matches the original (title/body/meta/references)', () => {
  const original = {
    title: 'T',
    body: 'B',
    meta_description: 'M',
    references: [{ title: '학회', url: 'https://a.example' }],
  }
  const edit = {
    title: 'T',
    body: 'B',
    meta_description: 'M',
    references: [{ title: '학회', url: 'https://a.example' }],
  }
  // dirty 가드: 편집 진입 직후 원본 그대로면 자동저장하지 않는다(복구용 스냅샷 보존).
  assert.equal(editFieldsDiffer(edit, original), false)
})

test('editFieldsDiffer flags title/body/meta and any reference change', () => {
  const original = {
    title: 'T',
    body: 'B',
    meta_description: 'M',
    references: [{ title: '학회', url: 'https://a.example' }],
  }
  assert.equal(editFieldsDiffer({ ...original, title: '다른 제목' }, original), true)
  assert.equal(editFieldsDiffer({ ...original, body: '다른 본문' }, original), true)
  assert.equal(editFieldsDiffer({ ...original, meta_description: '다른 요약' }, original), true)
  // 참고 자료 삭제 / URL 변경 / 추가 각각을 dirty로 감지한다.
  assert.equal(editFieldsDiffer({ ...original, references: [] }, original), true)
  assert.equal(
    editFieldsDiffer({ ...original, references: [{ title: '학회', url: 'https://b.example' }] }, original),
    true,
  )
  assert.equal(
    editFieldsDiffer(
      { ...original, references: [...original.references, { title: '추가', url: 'https://c.example' }] },
      original,
    ),
    true,
  )
})

test('cancelling an edit clears the stored snapshot so no false recovery banner appears next time', () => {
  const store = new Map<string, string>()
  const fakeWindow = {
    sessionStorage: {
      getItem: (k: string) => (store.has(k) ? store.get(k)! : null),
      setItem: (k: string, v: string) => { store.set(k, v) },
      removeItem: (k: string) => { store.delete(k) },
    },
  }
  const globals = globalThis as { window?: unknown }
  const originalWindow = globals.window
  globals.window = fakeWindow
  try {
    // 편집 중 자동저장으로 스냅샷이 남은 상태
    saveDraftSnapshot('h-1', 'c-1', { title: '편집됨', body: '본문', meta_description: '요약', references: [] })
    assert.ok(readDraftSnapshot('h-1', 'c-1'), '편집 중에는 스냅샷이 저장돼 있어야 한다')

    // 취소 핸들러가 호출하는 clearDraftSnapshot — 다음 진입 시 복구 배너가 뜨지 않도록 제거.
    clearDraftSnapshot('h-1', 'c-1')
    assert.equal(readDraftSnapshot('h-1', 'c-1'), null, '취소 후에는 스냅샷이 남지 않아야 한다')
  } finally {
    if (originalWindow === undefined) Reflect.deleteProperty(globals, 'window')
    else globals.window = originalWindow
  }
})
