export function adminServiceOrigin(configuredUrl) {
  return new URL(configuredUrl).origin
}

export async function probeAdminUnavailable(serviceUrl, fetchImpl = fetch) {
  try {
    await fetchImpl(`${serviceUrl}/login`, { signal: AbortSignal.timeout(2000) })
    return false
  } catch {
    return true
  }
}
