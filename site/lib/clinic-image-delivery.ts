/** Image request sizes track rendered slots, not the original upload dimensions.
 * Keep source URLs (including certification/version queries) unchanged.
 */
export const CLINIC_IMAGE_QUALITY = 75
export const CLINIC_IMAGE_SIZES = {
  hero: '(max-width: 720px) calc(100vw - 40px), (max-width: 920px) calc(100vw - 64px), (max-width: 1296px) 48vw, 600px',
  featured: '(max-width: 720px) calc(100vw - 40px), (max-width: 1296px) 52vw, 640px',
  soloFeatured: '(max-width: 720px) calc(100vw - 40px), (max-width: 1024px) calc(100vw - 64px), 760px',
  feed: '(max-width: 720px) calc(100vw - 86px), (max-width: 1024px) calc(100vw - 134px), (max-width: 1296px) calc(100vw - 166px), 1130px',
  card: '(max-width: 720px) calc(100vw - 80px), (max-width: 1080px) 44vw, 352px',
  band: '(max-width: 720px) calc(100vw - 40px), 720px',
  // 글 상세 보조 칸이 본문 아래로 내려가(2026-11-20 결정 12b) 표지는 한 열 폭 = min(856px, 100vw − 좌우 여백)이다.
  article: '(max-width: 720px) calc(100vw - 40px), (max-width: 920px) calc(100vw - 64px), 856px',
  portraitSolo: '(max-width: 720px) 104px, (max-width: 920px) 240px, 320px',
  portraitMultiple: '(max-width: 720px) 104px, (max-width: 1100px) 320px, 144px',
  gallery: '(max-width: 600px) calc(100vw - 40px), (max-width: 1023px) 46vw, 400px',
  galleryLead: '(max-width: 600px) calc(100vw - 40px), (max-width: 1023px) 46vw, (max-width: 1296px) 62vw, 800px',
} as const

export type ClinicImageStatus = 'pending' | 'ready' | 'failed'
/** An unrequested lazy image is not a failed image. */
export function readClinicImageStatus(image: Pick<HTMLImageElement, 'complete' | 'currentSrc' | 'naturalWidth' | 'naturalHeight'>): ClinicImageStatus {
  if (!image.complete || !image.currentSrc) return 'pending'
  return image.naturalWidth > 2 && image.naturalHeight > 2 ? 'ready' : 'failed'
}
