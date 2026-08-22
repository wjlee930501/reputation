// 병원별 공개 표면 시각 요소가 실제로 승인됐는지 추적한다.
//
// 감사에서 확인된 문제는 색·로고·카피가 코드에는 있는데 병원 데이터에는 없다는
// 것이었다. 여기서 다루는 항목은 로고·대표색·첫 화면 카피·정보 우선순위 넷이며
// 사진은 의도적으로 필수가 아니다 — 실사진이 없는 병원도 정상 운영 대상이다.

export type ClinicVisualStatus = 'done' | 'needed' | 'optional'

export interface ClinicVisualItem {
  key: 'logo' | 'primary_color' | 'hero_copy' | 'access_mode' | 'photos'
  label: string
  hint: string
  status: ClinicVisualStatus
  /** 승인이 없으면 플랫폼 기본값으로 노출된다는 뜻. 공개를 막지는 않는다. */
  blocksApproval: boolean
}

export interface ClinicVisualInput {
  logo_url?: string | null
  brand_primary_color?: string | null
  brand_accent_color?: string | null
  hero_headline?: string | null
  hero_description?: string | null
  site_access_mode?: string | null
  photo_count?: number | null
}

const HEX_COLOR = /^#[0-9a-f]{6}$/i
const ACCESS_MODES = new Set(['urgent', 'appointment', 'specialist'])

function trimmed(value: string | null | undefined): string {
  return (value ?? '').trim()
}

export function isApprovedBrandColor(value: string | null | undefined): boolean {
  return HEX_COLOR.test(trimmed(value))
}

export function isApprovedAccessMode(value: string | null | undefined): boolean {
  return ACCESS_MODES.has(trimmed(value))
}

export function buildClinicVisualChecklist(profile: ClinicVisualInput): ClinicVisualItem[] {
  const hasHeroCopy =
    trimmed(profile.hero_headline).length > 0 || trimmed(profile.hero_description).length > 0

  return [
    {
      key: 'logo',
      label: '공식 로고',
      hint: '병원이 실제로 쓰는 로고 이미지 URL을 등록합니다. 없으면 병원명 워드마크로 노출됩니다.',
      status: trimmed(profile.logo_url).length > 0 ? 'done' : 'needed',
      blocksApproval: true,
    },
    {
      key: 'primary_color',
      label: '대표색 1개',
      hint: '승인된 대표색 하나만 입력하면 나머지 단계와 대비 안전 색상은 공개 화면이 파생합니다.',
      status: isApprovedBrandColor(profile.brand_primary_color) ? 'done' : 'needed',
      blocksApproval: true,
    },
    {
      key: 'hero_copy',
      label: '첫 화면 카피',
      hint: '모든 병원에 같은 홍보 문장을 쓰지 않도록, 승인된 진료 원칙을 한두 문장으로 적습니다.',
      status: hasHeroCopy ? 'done' : 'needed',
      blocksApproval: true,
    },
    {
      key: 'access_mode',
      label: '첫 화면 정보 우선순위',
      hint: '당일·야간 진료형/예약·방문형/전문 진료형 중 하나를 고르면 첫 화면 순서가 병원에 맞춰집니다.',
      status: isApprovedAccessMode(profile.site_access_mode) ? 'done' : 'needed',
      blocksApproval: true,
    },
    {
      key: 'photos',
      label: '실사진',
      hint: '있으면 hero·갤러리에 쓰지만 필수가 아닙니다. 사진이 없어도 정보 중심으로 정상 노출됩니다.',
      status: (profile.photo_count ?? 0) > 0 ? 'done' : 'optional',
      blocksApproval: false,
    },
  ]
}

/** 아직 AE 승인이 남은 시각 항목. 사진은 여기 절대 포함되지 않는다. */
export function missingClinicVisualItems(profile: ClinicVisualInput): ClinicVisualItem[] {
  return buildClinicVisualChecklist(profile).filter(
    (item) => item.blocksApproval && item.status !== 'done',
  )
}

export function isClinicVisualApproved(profile: ClinicVisualInput): boolean {
  return missingClinicVisualItems(profile).length === 0
}

/** 사진 유무는 시각 승인 판단을 바꾸지 않는다. */
export function clinicVisualSummary(profile: ClinicVisualInput): string {
  const missing = missingClinicVisualItems(profile)
  if (missing.length === 0) return '공개 표면 시각 요소 승인 완료'
  return `승인 필요: ${missing.map((item) => item.label).join(', ')}`
}
