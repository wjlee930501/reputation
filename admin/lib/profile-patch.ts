/** Build the profile PATCH body without fields owned by dedicated asset endpoints. */
export function profilePatchPayload<T extends { logo_url?: unknown }>(
  profile: T,
): Omit<T, 'logo_url'> {
  return Object.fromEntries(
    Object.entries(profile).filter(([field]) => field !== 'logo_url'),
  ) as Omit<T, 'logo_url'>
}
