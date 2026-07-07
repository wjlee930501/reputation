// sitemap.ts가 요청 host로 "이 sitemap이 병원 전체를 담을지, 특정 병원 하나만 담을지"
// 판정하는 순수 함수 (robots-host.ts의 resolveSitemapUrl과 자매 모듈).
//
// 커스텀 도메인(또는 {slug}.{platform host} 하이브리드 서브도메인)에서 서빙되는
// sitemap.xml에는 그 병원 하나의 URL만 실어야 한다 — 그렇지 않으면 커스텀 도메인
// sitemap에 남의 병원 URL이 함께 노출된다. 플랫폼/로컬/run.app/vercel.app 호스트는
// 기존처럼 전체 병원을 담는다.

import { getPrimaryHostnames, isPrimaryHost, normalizeHostname } from './host-routing.ts'
import { platformSiteUrl } from './site-url.ts'

export type SitemapScope = { kind: 'all' } | { kind: 'host'; hostname: string }

export function resolveSitemapScope(hostHeader: string | null | undefined): SitemapScope {
  const platformBase = platformSiteUrl()
  const primaryHostnames = getPrimaryHostnames(platformBase)
  if (isPrimaryHost(hostHeader, primaryHostnames)) return { kind: 'all' }
  const hostname = normalizeHostname(hostHeader)
  // host 미상은 안전하게 전체 취급 — isPrimaryHost가 이미 host 미상을 primary로 처리하므로
  // 이 분기는 사실상 방어적 코드(정상 흐름에서는 도달하지 않는다).
  if (!hostname) return { kind: 'all' }
  return { kind: 'host', hostname }
}
