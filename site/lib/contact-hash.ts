export const CONTACT_HASH = '#contact'

// 2026-09-23 랜딩 개편 전의 도입문의 앵커(`#lead`, 뒤에 UTM 등이 붙은 형태 포함).
const LEGACY_CONTACT_HASH = /^#lead(?:$|[^A-Za-z0-9_-])/

/**
 * Browsers treat everything after `#` as the fragment, including a misplaced
 * query string. Return the canonical fragment only when `contact` has trailing
 * data; clean and unrelated fragments must be left alone.
 */
export function normalizeContactHash(hash: string): typeof CONTACT_HASH | null {
  if (hash === CONTACT_HASH) return null
  if (hash.startsWith(CONTACT_HASH)) return CONTACT_HASH
  // 개편 전에 광고·문자·공지로 나간 `/#lead` 링크가 폼 대신 페이지 맨 위에서
  // 멈추지 않게 같은 폼으로 보낸다.
  if (LEGACY_CONTACT_HASH.test(hash)) return CONTACT_HASH
  return null
}
