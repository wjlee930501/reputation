// robots.txt sitemap 디렉티브를 요청 origin 기준으로 만든다 (순수 함수 — 테스트 가능).
//
// 커스텀 도메인으로 서빙 중인 병원은 robots.txt도 그 도메인에서 응답되므로, sitemap
// 포인터를 플랫폼 호스트로 고정하면 크롤러가 엉뚱한 origin을 본다. 플랫폼/로컬 호스트는
// 기존처럼 platformSiteUrl을 그대로 쓰고, 커스텀 호스트만 요청 origin으로 바꾼다.

import { getPrimaryHostnames, isPrimaryHost, normalizeHostname } from './host-routing.ts'
import { platformSiteUrl } from './site-url.ts'

/**
 * robots.txt에 실을 sitemap 절대 URL.
 * - 플랫폼/로컬/run.app/vercel.app 호스트 또는 host 미상 → platformSiteUrl (기존 동작 유지)
 * - 커스텀 호스트 → `${proto}://${host}` 기준 (proto 미상 시 https)
 */
export function resolveSitemapUrl(
  hostHeader: string | null | undefined,
  protoHeader: string | null | undefined,
): string {
  const platformBase = platformSiteUrl()
  const primaryHostnames = getPrimaryHostnames(platformBase)
  if (isPrimaryHost(hostHeader, primaryHostnames)) {
    return `${platformBase}/sitemap.xml`
  }
  const hostname = normalizeHostname(hostHeader)
  if (!hostname) return `${platformBase}/sitemap.xml`
  // x-forwarded-proto는 "https,http"처럼 콤마로 누적될 수 있어 첫 토큰만 본다.
  const proto = (protoHeader || '').split(',', 1)[0]?.trim().toLowerCase() || 'https'
  const scheme = proto === 'http' ? 'http' : 'https'
  // origin은 검증된 hostname + 엄격히 파싱한 숫자 포트로만 구성한다 (Host 헤더 스푸핑/주입 차단).
  return `${scheme}://${hostname}${extractPort(hostHeader)}/sitemap.xml`
}

// Host 헤더에서 포트만 안전하게 추출한다 — 숫자 1~5자리(<=65535)만 허용, 그 외엔 빈 문자열.
function extractPort(hostHeader: string | null | undefined): string {
  const raw = hostHeader?.trim() ?? ''
  let portPart = ''
  if (raw.startsWith('[')) {
    const end = raw.indexOf(']')
    portPart = end >= 0 ? raw.slice(end + 1) : ''
  } else if (raw.includes(':') && raw.indexOf(':') === raw.lastIndexOf(':')) {
    portPart = raw.slice(raw.lastIndexOf(':'))
  }
  if (portPart.startsWith(':')) {
    const p = portPart.slice(1)
    if (/^\d{1,5}$/.test(p) && Number(p) <= 65535) return `:${p}`
  }
  return ''
}
