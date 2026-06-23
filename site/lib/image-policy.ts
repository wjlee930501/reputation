export function isPublicApiAssetUrl(src: string | null | undefined): boolean {
  if (!src) return false
  try {
    const p = new URL(src, 'http://localhost').pathname
    // /assets/{id} (원장·병원 사진) 와 /contents/{id}/image (콘텐츠 대표 이미지) 모두
    // backend 프록시(요청마다 302 signed)라 next/image 최적화를 우회해 직접 src로 렌더한다.
    return (
      /^\/api\/v1\/public\/hospitals\/[^/]+\/assets\/[^/]+/.test(p) ||
      /^\/api\/v1\/public\/hospitals\/[^/]+\/contents\/[^/]+\/image/.test(p)
    )
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

// next.config.mjs images.remotePatterns가 next/image 최적화를 허용하는 호스트.
// 여기에 없는 절대 외부 URL을 next/image로 최적화하면 400 'url not allowed'가 나므로
// 해당 호스트는 최적화를 우회(unoptimized)해 원본 src를 그대로 렌더한다.
function isOptimizableExternalHost(hostname: string): boolean {
  if (hostname === 'storage.googleapis.com' || hostname.endsWith('.storage.googleapis.com')) {
    return true
  }
  const backendHosts = [
    process.env.NEXT_PUBLIC_BACKEND_URL,
    process.env.NEXT_PUBLIC_API_URL,
    process.env.BACKEND_URL,
  ]
  for (const value of backendHosts) {
    if (!value) continue
    try {
      if (new URL(value).hostname === hostname) return true
    } catch {
      // 무시 — 잘못된 env 값
    }
  }
  return false
}

// next/image remotePatterns 허용 목록에 없는 절대 외부 URL인지 판정.
export function isOffAllowlistExternalUrl(src: string | null | undefined): boolean {
  if (!src) return false
  const trimmed = src.trim()
  if (!(trimmed.startsWith('http://') || trimmed.startsWith('https://'))) return false
  try {
    return !isOptimizableExternalHost(new URL(trimmed).hostname)
  } catch {
    return false
  }
}

export function shouldBypassNextImageOptimization(src: string | null | undefined): boolean {
  return isPublicApiAssetUrl(src) || isBackendAssetUrl(src) || isOffAllowlistExternalUrl(src)
}
