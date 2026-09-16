'use client'

import Image from 'next/image'
import type { ReactNode } from 'react'
import { CLINIC_IMAGE_QUALITY, CLINIC_IMAGE_SIZES } from '@/lib/clinic-image-delivery'
import { useClinicImage } from '@/lib/use-clinic-image'
import { isOffAllowlistExternalUrl } from '@/lib/image-policy'

interface Props {
  src: string | null
  alt: string
  wrapperClassName: string
  fallbackClassName?: string
  fallback: ReactNode
  sizes?: string
  priority?: boolean
}

/** Verified portraits are discoverable in SSR, without waiting for hydration.
 * The permanent monogram remains below the image; the ref catches pre-hydration
 * errors, and source-scoped state allows replacement images to recover.
 */
export function ClinicAvatar({ src, alt, wrapperClassName, fallbackClassName = '',
  fallback, sizes = CLINIC_IMAGE_SIZES.portraitSolo, priority = false }: Props) {
  const { ref: imageRef, onLoad, onError, status, showImage } = useClinicImage(src)
  const decorative = alt === ''
  return (
    <div className={`${wrapperClassName} ${fallbackClassName}`.trim()}
      role={decorative ? undefined : 'img'} aria-label={decorative ? undefined : alt}
      aria-hidden={decorative || undefined} data-image-state={status}>
      {fallback}
      {showImage && src && (
        <Image key={src} ref={imageRef} src={src} alt="" aria-hidden="true"
          fill sizes={sizes} quality={CLINIC_IMAGE_QUALITY}
          fetchPriority={priority ? 'high' : 'auto'}
          loading={priority ? 'eager' : 'lazy'}
          unoptimized={isOffAllowlistExternalUrl(src)}
          onError={onError} onLoad={onLoad}
          style={{ objectFit: 'cover' }} />
      )}
    </div>
  )
}
