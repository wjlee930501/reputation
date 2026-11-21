'use client'

import Image from 'next/image'
import type { ReactNode } from 'react'
import { CLINIC_IMAGE_QUALITY, CLINIC_IMAGE_SIZES } from '@/lib/clinic-image-delivery'
import { useClinicImage } from '@/lib/use-clinic-image'
import { isOffAllowlistExternalUrl } from '@/lib/image-policy'
import { isPreviewAssetUrl } from '@/lib/site-preview-assets'

interface Props {
  src: string | null
  alt: string
  wrapperClassName: string
  fallbackClassName?: string
  fallback: ReactNode
  sizes?: string
  priority?: boolean
  /**
   * 섹션 설정이 바꾼 사진 비율·초점(`physicianPhotoLook`). 사진을 보일 때만 쓴다 — 이름 첫 글자 타일은
   * 지금 모양 그대로다. null이면 지금 마크업과 같다.
   */
  look?: { aspectRatio: string | null; objectPosition: string | null } | null
}

/** Verified portraits are discoverable in SSR, without waiting for hydration.
 * The permanent monogram remains below the image; the ref catches pre-hydration
 * errors, and source-scoped state allows replacement images to recover.
 */
export function ClinicAvatar({ src, alt, wrapperClassName, fallbackClassName = '',
  fallback, sizes = CLINIC_IMAGE_SIZES.portraitSolo, priority = false, look = null }: Props) {
  const { ref: imageRef, onLoad, onError, status, showImage } = useClinicImage(src)
  const decorative = alt === ''
  // 머리글자 모양(fallbackClassName)은 사진이 없거나 못 불러왔을 때만 붙인다. 늘 붙이면 사진이
  // 있어도 머리글자 타일의 크기 규칙(정사각 220px)이 사진 상자를 잘랐다(2026-11-20, 기본형·B).
  const showFallbackShape = !src || status === 'failed'
  const aspectRatio = !showFallbackShape && look?.aspectRatio ? look.aspectRatio : null
  return (
    <div className={`${wrapperClassName} ${showFallbackShape ? fallbackClassName : ''}`.trim()}
      style={aspectRatio ? { aspectRatio } : undefined}
      role={decorative ? undefined : 'img'} aria-label={decorative ? undefined : alt}
      aria-hidden={decorative || undefined} data-image-state={status}>
      {fallback}
      {showImage && src && (
        <Image key={src} ref={imageRef} src={src} alt="" aria-hidden="true"
          fill sizes={sizes} quality={CLINIC_IMAGE_QUALITY}
          fetchPriority={priority ? 'high' : 'auto'}
          loading={priority ? 'eager' : 'lazy'}
          unoptimized={isOffAllowlistExternalUrl(src) || isPreviewAssetUrl(src)}
          onError={onError} onLoad={onLoad}
          style={look?.objectPosition ? { objectFit: 'cover', objectPosition: look.objectPosition } : { objectFit: 'cover' }} />
      )}
    </div>
  )
}
