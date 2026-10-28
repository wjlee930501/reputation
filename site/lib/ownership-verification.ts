import type { Metadata } from 'next'

import { getApiBase } from './config.ts'
import { normalizeHostname } from './host-routing.ts'

const DAUM_WEBMASTER_PATTERN =
  /^#DaumWebMasterTool:[A-Za-z0-9+/=_-]+:[A-Za-z0-9+/=_-]+$/
const NAVER_SITE_VERIFICATION_PATTERN = /^[A-Za-z0-9._:-]+$/
const LOOKUP_TIMEOUT_MS = 5000

export function validDaumWebmasterVerification(value: unknown): string | null {
  if (typeof value !== 'string') return null
  const phrase = value.trim()
  return DAUM_WEBMASTER_PATTERN.test(phrase) ? phrase : null
}

export function ownershipVerificationMetadata(
  naverValue: string | null | undefined,
  googleValue: string | null | undefined,
): Metadata['verification'] | undefined {
  const naver = (naverValue ?? '').trim()
  const google = (googleValue ?? '').trim()
  if (!google && !NAVER_SITE_VERIFICATION_PATTERN.test(naver)) return undefined
  return {
    ...(google ? { google } : {}),
    ...(NAVER_SITE_VERIFICATION_PATTERN.test(naver)
      ? { other: { 'naver-site-verification': naver } }
      : {}),
  }
}

/**
 * 요청 custom host에 저장된 Daum 공개 증명 문구를 읽는다.
 *
 * API 장애나 잘못된 응답은 기존 robots 지시를 깨지 않고 문구만 생략한다. 반환된
 * aeo_domain을 다시 비교해 플랫폼 서브도메인이나 다른 tenant의 문구가 섞이지 않게 한다.
 */
export async function fetchDaumVerificationForHost(host: string): Promise<string | null> {
  const hostname = normalizeHostname(host)
  const apiBase = getApiBase(false)
  if (!hostname || !apiBase) return null

  try {
    const response = await fetch(
      `${apiBase}/site/hospitals/by-domain/${encodeURIComponent(hostname)}`,
      { cache: 'no-store', signal: AbortSignal.timeout(LOOKUP_TIMEOUT_MS) },
    )
    if (!response.ok) return null
    const payload: unknown = await response.json()
    if (typeof payload !== 'object' || payload === null) return null
    const record = payload as Record<string, unknown>
    if (normalizeHostname(record.aeo_domain as string | null | undefined) !== hostname) return null
    return validDaumWebmasterVerification(record.daum_webmaster_verification)
  } catch (error) {
    if (error instanceof DOMException || error instanceof TypeError || error instanceof SyntaxError) {
      return null
    }
    throw error
  }
}
