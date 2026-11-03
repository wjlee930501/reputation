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

const META_SITE_VERIFICATION_PATTERN = /^[A-Za-z0-9._:+/=-]{8,255}$/

function validMetaToken(value: string | null | undefined): string | null {
  const token = (value ?? '').trim()
  return META_SITE_VERIFICATION_PATTERN.test(token) ? token : null
}

/**
 * 병원 레이아웃의 검색엔진 소유 확인 메타.
 *
 * Google은 플랫폼 공용 토큰(배포 환경값)과 병원별 토큰을 함께 싣는다. 자기 도메인 병원은
 * 공용 토큰으로 Search Console에 등록할 수 없어 병원 행의 토큰이 따로 필요하다.
 * Bing은 병원별 `msvalidate.01`만 있다. 형식이 맞지 않는 값은 싣지 않는다.
 */
export function ownershipVerificationMetadata(
  naverValue: string | null | undefined,
  googleValue: string | null | undefined,
  hospitalGoogleValue?: string | null,
  bingValue?: string | null,
): Metadata['verification'] | undefined {
  const naver = (naverValue ?? '').trim()
  const googleTokens = [...new Set([(googleValue ?? '').trim(), validMetaToken(hospitalGoogleValue) ?? ''])].filter(
    Boolean,
  )
  const bing = validMetaToken(bingValue)
  const other: Record<string, string> = {}
  if (NAVER_SITE_VERIFICATION_PATTERN.test(naver)) other['naver-site-verification'] = naver
  if (bing) other['msvalidate.01'] = bing
  if (googleTokens.length === 0 && Object.keys(other).length === 0) return undefined
  return {
    ...(googleTokens.length === 1 ? { google: googleTokens[0] } : {}),
    ...(googleTokens.length > 1 ? { google: googleTokens } : {}),
    ...(Object.keys(other).length > 0 ? { other } : {}),
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
