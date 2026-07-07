// 세션 만료(401) → 로그인 리다이렉트가 발생하면 편집 중이던 콘텐츠 내용이 그대로
// 사라진다. 편집 필드가 바뀔 때마다 병원id+콘텐츠id 키로 sessionStorage에 스냅샷을
// 남겨, 재로그인 후 같은 탭에서 복구 안내 배너로 이어서 작업할 수 있게 한다.
// (세션 종료와 함께 사라지는 sessionStorage를 사용 — 공용 PC에 남지 않는다.)

export interface ContentDraftSnapshot {
  title: string
  body: string
  meta_description: string
  references: Array<{ title: string; url: string }>
  savedAt: number
}

const KEY_PREFIX = 'reputation.contentDraft'

export function buildDraftSnapshotKey(hospitalId: string, contentId: string): string {
  return `${KEY_PREFIX}.${hospitalId}.${contentId}`
}

export function serializeDraftSnapshot(
  draft: Omit<ContentDraftSnapshot, 'savedAt'>,
  savedAt: number = Date.now(),
): string {
  return JSON.stringify({ ...draft, savedAt })
}

function isReferenceList(value: unknown): value is Array<{ title: string; url: string }> {
  return (
    Array.isArray(value) &&
    value.every((ref) => ref && typeof ref === 'object' && typeof (ref as { title: unknown }).title === 'string' && typeof (ref as { url: unknown }).url === 'string')
  )
}

/** 손상되었거나 알 수 없는 형태의 저장값은 조용히 버린다(복구 기능은 실패해도 편집을 막지 않아야 한다). */
export function parseDraftSnapshot(raw: string | null): ContentDraftSnapshot | null {
  if (!raw) return null
  let parsed: unknown
  try {
    parsed = JSON.parse(raw)
  } catch {
    return null
  }
  if (
    !parsed ||
    typeof parsed !== 'object' ||
    typeof (parsed as Record<string, unknown>).title !== 'string' ||
    typeof (parsed as Record<string, unknown>).body !== 'string' ||
    typeof (parsed as Record<string, unknown>).meta_description !== 'string' ||
    typeof (parsed as Record<string, unknown>).savedAt !== 'number' ||
    !isReferenceList((parsed as Record<string, unknown>).references)
  ) {
    return null
  }
  return parsed as ContentDraftSnapshot
}

export function saveDraftSnapshot(
  hospitalId: string,
  contentId: string,
  draft: Omit<ContentDraftSnapshot, 'savedAt'>,
): void {
  if (typeof window === 'undefined') return
  window.sessionStorage.setItem(buildDraftSnapshotKey(hospitalId, contentId), serializeDraftSnapshot(draft))
}

export function readDraftSnapshot(hospitalId: string, contentId: string): ContentDraftSnapshot | null {
  if (typeof window === 'undefined') return null
  return parseDraftSnapshot(window.sessionStorage.getItem(buildDraftSnapshotKey(hospitalId, contentId)))
}

export function clearDraftSnapshot(hospitalId: string, contentId: string): void {
  if (typeof window === 'undefined') return
  window.sessionStorage.removeItem(buildDraftSnapshotKey(hospitalId, contentId))
}

/** 저장된 스냅샷이 현재 편집중인 값과 실질적으로 다를 때만 복구 배너를 띄우기 위한 비교. */
export function draftDiffersFromCurrent(
  draft: ContentDraftSnapshot,
  current: { title: string; body: string; meta_description: string },
): boolean {
  return draft.title !== current.title || draft.body !== current.body || draft.meta_description !== current.meta_description
}

export interface EditableDraftFields {
  title: string
  body: string
  meta_description: string
  references: Array<{ title: string; url: string }>
}

function referencesDiffer(
  a: Array<{ title: string; url: string }>,
  b: Array<{ title: string; url: string }>,
): boolean {
  if (a.length !== b.length) return true
  for (let i = 0; i < a.length; i += 1) {
    if (a[i].title !== b[i].title || a[i].url !== b[i].url) return true
  }
  return false
}

/**
 * 자동저장 dirty 가드 — 편집 필드가 원본과 실질적으로 다를 때만 true.
 * 편집 진입 직후(원본 그대로)엔 스냅샷을 저장하지 않아, 세션 만료 전 남겨둔 복구용
 * 스냅샷을 미편집 원본으로 덮어써 이중 401/새로고침 시 복구 불능이 되는 것을 막는다.
 */
export function editFieldsDiffer(edit: EditableDraftFields, original: EditableDraftFields): boolean {
  if (
    edit.title !== original.title ||
    edit.body !== original.body ||
    edit.meta_description !== original.meta_description
  ) {
    return true
  }
  return referencesDiffer(edit.references, original.references)
}
