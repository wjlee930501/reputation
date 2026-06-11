type HeaderLike = {
  get(name: string): string | null
}

function isLikelyIp(value: string): boolean {
  // IPv4 dotted-quad or IPv6 (loose check — enough to reject junk).
  if (/^\d{1,3}(\.\d{1,3}){3}$/.test(value)) return true
  return value.includes(':') && /^[0-9a-fA-F:.]+$/.test(value)
}

// 실제 방문자 IP 추출 — GCP 외부 Application LB 뒤 기준 (GCLB 단독 배포).
// 1. X-Forwarded-For가 1차 기준: LB가 "<client>, <lb>"를 뒤에 붙이므로 실제 방문자는
//    SECOND-FROM-RIGHT. leftmost는 클라이언트가 위조 가능.
//    (프로덕션 Cloud Run ingress는 INTERNAL_LOAD_BALANCER — 항상 LB를 거친다.
//    단일 항목 XFF는 로컬 dev에서만 발생.)
// 2. x-real-ip / x-vercel-forwarded-for는 XFF가 아예 없을 때만 최후 fallback으로 사용.
//    GCLB는 인바운드 x-real-ip를 제거하지 않으므로 클라이언트가 임의 설정 가능 —
//    이 값을 우선하면 백엔드 rate-limit key와 PIPA consent_ip가 공격자 선택값이 된다.
// admin/lib/security.ts의 clientIpFromForwardedHeaders와 동일 정책.
export function clientIpFromForwardedHeaders(headers: HeaderLike): string | null {
  const forwarded = headers.get('x-forwarded-for')
  if (forwarded) {
    const entries = forwarded.split(',').map((entry) => entry.trim()).filter(Boolean)
    const candidate = entries.length >= 2 ? entries[entries.length - 2] : entries[0]
    // XFF가 존재하면 그 결과만 신뢰한다 — 파싱 실패 시에도 위조 가능한
    // x-real-ip로 내려가지 않는다.
    return candidate && isLikelyIp(candidate) ? candidate : null
  }

  const fallbackIp = (
    headers.get('x-real-ip') ||
    headers.get('x-vercel-forwarded-for') ||
    ''
  ).trim()
  if (fallbackIp && isLikelyIp(fallbackIp)) {
    return fallbackIp
  }
  return null
}
