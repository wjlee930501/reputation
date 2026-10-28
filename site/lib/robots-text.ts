import { AI_SEARCH_USER_AGENTS } from './ai-crawlers.ts'
import { validDaumWebmasterVerification } from './ownership-verification.ts'
import { ROBOTS_ALLOWED_PATHS, ROBOTS_DISALLOWED_PATHS } from './robots-policy.ts'

function rules(userAgents: readonly string[]): string[] {
  return [
    ...userAgents.map((userAgent) => `User-Agent: ${userAgent}`),
    ...ROBOTS_ALLOWED_PATHS.map((path) => `Allow: ${path}`),
    ...ROBOTS_DISALLOWED_PATHS.map((path) => `Disallow: ${path}`),
  ]
}

/**
 * Next MetadataRoute.Robots가 만들던 지시 순서와 공백을 보존하면서, 표현할 수 없던
 * Daum 소유 확인 주석만 Sitemap 다음 마지막 줄에 붙인다.
 */
export function renderRobotsText(
  sitemap: string,
  daumWebmasterVerification?: string | null,
): string {
  const daumPhrase = validDaumWebmasterVerification(daumWebmasterVerification)
  return [
    ...rules(['*']),
    '',
    ...rules(AI_SEARCH_USER_AGENTS),
    '',
    `Sitemap: ${sitemap}`,
    ...(daumPhrase ? [daumPhrase] : []),
    '',
  ].join('\n')
}
