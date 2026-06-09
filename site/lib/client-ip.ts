type HeaderLike = {
  get(name: string): string | null
}

function isLikelyIp(value: string): boolean {
  // IPv4 dotted-quad or IPv6 (loose check — enough to reject junk).
  if (/^\d{1,3}(\.\d{1,3}){3}$/.test(value)) return true
  return value.includes(':') && /^[0-9a-fA-F:.]+$/.test(value)
}

// 실제 방문자 IP 추출 — 플랫폼 제어 헤더 우선, 클라이언트 조작 가능 값은 보수적으로.
// 1. x-vercel-forwarded-for / x-real-ip — Vercel 등 플랫폼이 설정.
// 2. GCP 외부 Application LB 뒤의 X-Forwarded-For: LB가 "<client>, <lb>"를 뒤에
//    붙이므로 실제 방문자는 SECOND-FROM-RIGHT. leftmost는 클라이언트가 위조 가능.
//    (프로덕션 Cloud Run ingress는 INTERNAL_LOAD_BALANCER — 항상 LB를 거친다.
//    단일 항목 XFF는 로컬 dev에서만 발생.)
// admin/lib/security.ts의 clientIpFromForwardedHeaders와 동일 정책.
export function clientIpFromForwardedHeaders(headers: HeaderLike): string | null {
  const platformIp = (
    headers.get('x-vercel-forwarded-for') ||
    headers.get('x-real-ip') ||
    ''
  ).trim()
  if (platformIp && isLikelyIp(platformIp)) {
    return platformIp
  }

  const forwarded = headers.get('x-forwarded-for')
  if (forwarded) {
    const entries = forwarded.split(',').map((entry) => entry.trim()).filter(Boolean)
    const candidate = entries.length >= 2 ? entries[entries.length - 2] : entries[0]
    if (candidate && isLikelyIp(candidate)) {
      return candidate
    }
  }
  return null
}
