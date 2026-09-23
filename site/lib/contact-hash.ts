export const CONTACT_HASH = '#contact'

/**
 * Browsers treat everything after `#` as the fragment, including a misplaced
 * query string. Return the canonical fragment only when `contact` has trailing
 * data; clean and unrelated fragments must be left alone.
 */
export function normalizeContactHash(hash: string): typeof CONTACT_HASH | null {
  if (hash === CONTACT_HASH || !hash.startsWith(CONTACT_HASH)) return null
  return CONTACT_HASH
}
