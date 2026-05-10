export function isPublicApiAssetUrl(src: string | null | undefined): boolean {
  if (!src) return false
  try {
    const parsed = new URL(src, 'http://localhost')
    return /^\/api\/v1\/public\/hospitals\/[^/]+\/assets\/[^/]+/.test(parsed.pathname)
  } catch {
    return false
  }
}

export function isBackendAssetUrl(src: string | null | undefined): boolean {
  if (!src) return false
  try {
    const parsed = new URL(src, 'http://localhost')
    return /^\/assets\/[^/]+\/[^/]+/.test(parsed.pathname)
  } catch {
    return false
  }
}

export function shouldBypassNextImageOptimization(src: string | null | undefined): boolean {
  return isPublicApiAssetUrl(src) || isBackendAssetUrl(src)
}
