import { selectClinicDirectorImage } from './clinic-theme.ts'
import { resolveAssetUrl, type Hospital } from './hospital-payload.ts'

/** 공유 이미지가 없을 때 쓰는 플랫폼 기본 사진(병원 사진이 아니다). */
export const PLATFORM_SHARE_IMAGE = '/landing/reputation-clinic-trust-interior.png'

/**
 * 공유(카카오·문자·OG) 이미지와 그 대체 글. 대체 글은 **실제로 고른 사진**을 말한다 — 원장 사진일 때만 원장 이름,
 * 섹션 설정에서 고른 병원 사진·플랫폼 기본 사진이면 병원 이름이다(전에는 사진과 무관하게 '○○ 원장'이라 공개 조건을
 * 못 넘은 병원에서 플랫폼 실내 사진에 원장 이름이 붙었다).
 */
export function selectShareImage(
  hospital: Pick<Hospital, 'name' | 'director_name' | 'photos' | 'site_config'> & Parameters<typeof selectClinicDirectorImage>[0],
): { url: string; alt: string } {
  const shareId = hospital.site_config?.share?.imageAssetId
  const shareImage = shareId ? resolveAssetUrl(hospital.photos.find((photo) => photo.id === shareId)?.url ?? null) : null
  if (shareImage) return { url: shareImage, alt: hospital.name }
  const director = selectClinicDirectorImage(hospital)
  if (director) return { url: director, alt: hospital.director_name ? `${hospital.director_name} 원장` : hospital.name }
  return { url: PLATFORM_SHARE_IMAGE, alt: hospital.name }
}
