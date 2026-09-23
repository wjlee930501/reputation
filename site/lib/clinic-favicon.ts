import {
  CLINIC_INK,
  DEFAULT_CLINIC_PRIMARY,
  contrastRatio,
  normalizeClinicColor,
} from './clinic-theme.ts'
import type { Hospital } from './hospital-payload.ts'

// 병원 페이지의 탭 아이콘. 플랫폼(Re:putation) 심볼을 병원 표면에 내보내지 않고,
// 승인된 대표색 바탕에 병원명 첫 글자를 얹은 모노그램을 자동으로 만든다.
// 사람이 병원마다 아이콘을 준비하지 않아도 새 병원이 바로 자기 아이콘을 갖는다.

const WHITE = '#FFFFFF'
// 흰 글자는 큰 글씨 기준(WCAG AA-large 3:1)만 넘으면 쓴다. 대비가 가장 큰 색을 고르면
// 주황·청록 같은 채도 높은 대표색도 짙은 글자가 되어 브랜드 인상이 흐려진다.
const WHITE_TEXT_MIN_CONTRAST = 3

// 번들한 Pretendard 서브셋(assets/fonts)이 가진 글자만 그린다. 그 밖의 글자는
// 빈 모노그램으로 두어 렌더러가 외부 폰트를 찾으러 나가지 않게 한다.
const RENDERABLE_CHAR = /^[가-힣A-Za-z0-9]$/
const LETTER_OR_DIGIT = /[\p{L}\p{N}]/u

// 규칙(색·글자 선택)을 바꾸면 올린다 — 브라우저·CDN에 남은 옛 아이콘을 갈아 끼운다.
const FAVICON_RULE_VERSION = 1

export type ClinicFaviconSpec = Readonly<{
  letter: string
  background: string
  foreground: string
}>

export function clinicMonogramLetter(name: string | null | undefined): string {
  const first = (name || '').trim().match(LETTER_OR_DIGIT)?.[0] ?? ''
  const letter = first.toUpperCase()
  return RENDERABLE_CHAR.test(letter) ? letter : ''
}

export function clinicFaviconSpec(
  hospital: Pick<Hospital, 'name' | 'brand_primary_color'>,
): ClinicFaviconSpec {
  const background = normalizeClinicColor(hospital.brand_primary_color, DEFAULT_CLINIC_PRIMARY)
  const foreground =
    contrastRatio(WHITE, background) >= WHITE_TEXT_MIN_CONTRAST ? WHITE : CLINIC_INK
  return { letter: clinicMonogramLetter(hospital.name), background, foreground }
}

// FNV-1a 32비트 — 캐시 무효화용 짧은 지문일 뿐 보안 용도가 아니다.
function fingerprint(value: string): string {
  let hash = 0x811c9dc5
  for (const char of value) {
    hash ^= char.codePointAt(0) ?? 0
    hash = Math.imul(hash, 0x01000193)
  }
  return (hash >>> 0).toString(36)
}

export function clinicFaviconVersion(spec: ClinicFaviconSpec): string {
  return fingerprint(`${FAVICON_RULE_VERSION}|${spec.letter}|${spec.background}|${spec.foreground}`)
}

export type ClinicFaviconVariant = 'icon' | 'apple'

/**
 * `/favicon/...`은 host-routing의 예약 경로라 커스텀 도메인에서도 병원 허브로
 * rewrite되지 않고 이 라우트로 바로 온다.
 */
export function clinicFaviconHref(
  slug: string,
  hospital: Pick<Hospital, 'name' | 'brand_primary_color'>,
  variant: ClinicFaviconVariant,
): string {
  const version = clinicFaviconVersion(clinicFaviconSpec(hospital))
  const query = new URLSearchParams({ v: version })
  if (variant === 'apple') query.set('variant', 'apple')
  return `/favicon/${encodeURIComponent(slug)}?${query.toString()}`
}
