'use client'

import { useCallback, useState, type SyntheticEvent } from 'react'
import { readClinicImageStatus, type ClinicImageStatus } from './clinic-image-delivery.ts'

/** SSR discovery plus permanent fallback; hydrate-time refs catch early load errors.
 * Status is scoped to the source, so a new image never inherits the old failure.
 */
export function useClinicImage(src: string | null | undefined) {
  const [result, setResult] = useState<{ src: typeof src; status: ClinicImageStatus }>({ src, status: 'pending' })
  const inspect = useCallback((node: HTMLImageElement | null) => {
    if (!node) return
    const status = readClinicImageStatus(node)
    if (status !== 'pending') setResult({ src, status })
  }, [src])
  const onLoad = useCallback((event: SyntheticEvent<HTMLImageElement>) => inspect(event.currentTarget), [inspect])
  const onError = useCallback(() => setResult({ src, status: 'failed' }), [src])
  const status = !src ? 'failed' : result.src === src ? result.status : 'pending'
  return { ref: inspect, onLoad, onError, status, showImage: Boolean(src) && status !== 'failed' }
}
