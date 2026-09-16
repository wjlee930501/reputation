'use client'

import Image from 'next/image'
import { ContentMotif } from '@/components/brand'
import { CLINIC_IMAGE_QUALITY, CLINIC_IMAGE_SIZES } from '@/lib/clinic-image-delivery'
import { useClinicImage } from '@/lib/use-clinic-image'
import { isOffAllowlistExternalUrl } from '@/lib/image-policy'

interface Props {
  type: string
  src?: string | null
  alt?: string
  variant?: 'featured' | 'card' | 'band'
  className?: string
  /** A page's leading image opts in; a large below-fold card does not. */
  priority?: boolean
  sizes?: string
}

/** Source URLs and image certification queries are never rewritten here.
 * SSR includes the real image plus a permanent decorative underlay, so loading
 * or errors do not leave an empty box. No opacity gate requires JavaScript.
 */
export function ContentCover({ type, src, alt = '', variant = 'card', className = '',
  priority = false, sizes }: Props) {
  const { ref: imageRef, onLoad, onError, status, showImage } = useClinicImage(src)
  return (
    <div className={`clinic-cover clinic-cover--${variant} clinic-cover--${(type || 'FAQ').toLowerCase()} ${className}`.trim()}
      aria-hidden={alt ? undefined : true} data-image-state={status}>
      <span className="clinic-cover-watermark" aria-hidden="true"><ContentMotif type={type} /></span>
      <span className="clinic-cover-motif" aria-hidden="true"><ContentMotif type={type} /></span>
      {showImage && src && (
        <Image key={src} ref={imageRef} src={src} alt={alt} fill
          sizes={sizes ?? CLINIC_IMAGE_SIZES[variant]}
          fetchPriority={priority ? 'high' : 'auto'}
          loading={priority ? 'eager' : 'lazy'} quality={CLINIC_IMAGE_QUALITY}
          unoptimized={isOffAllowlistExternalUrl(src)}
          onError={onError} onLoad={onLoad} className="clinic-cover-img" />
      )}
    </div>
  )
}
