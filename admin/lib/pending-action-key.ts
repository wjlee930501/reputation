/**
 * Keep one request key while the caller cannot tell whether a POST committed.
 * The caller removes the fingerprint only after a definitive response.
 */
export function getOrCreatePendingActionKey(
  cache: Map<string, string>,
  fingerprint: string,
  create: () => string,
): string {
  const existing = cache.get(fingerprint)
  if (existing) return existing
  const created = create()
  cache.set(fingerprint, created)
  return created
}
