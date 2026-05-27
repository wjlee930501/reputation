'use client'

import { useCallback, useState, type ReactNode } from 'react'

/** 실질적으로 비어있는(1x1 placeholder) 이미지인지 판정 */
function isBlankImage(img: HTMLImageElement): boolean {
  return (img.naturalWidth || 0) <= 2
}

interface Props {
  src: string | null
  alt: string
  /** 컨테이너 클래스 (예: clinic-curator-portrait) */
  wrapperClassName: string
  /** 이미지가 없거나 깨졌을 때 컨테이너에 추가할 클래스 (예: clinic-curator-portrait--monogram) */
  fallbackClassName?: string
  /** 이미지 fallback 콘텐츠 (모노그램 등) */
  fallback: ReactNode
}

/**
 * 원장 사진처럼 외부 자산이 비어있거나(1x1 placeholder) 로드 실패할 수 있는 이미지를
 * 우아하게 모노그램 등으로 대체한다. null·로드 실패·실질적 빈 이미지를 모두 처리.
 */
export function ClinicAvatar({ src, alt, wrapperClassName, fallbackClassName = '', fallback }: Props) {
  const [failed, setFailed] = useState(!src)
  const decorative = alt === ''

  // 캐시된 이미지는 React가 onLoad를 붙이기 전에 이미 complete 상태일 수 있어
  // ref 콜백에서 마운트 시점에 한 번 더 검사한다.
  const imgRef = useCallback((node: HTMLImageElement | null) => {
    if (node && node.complete && isBlankImage(node)) setFailed(true)
  }, [])

  if (!failed && src) {
    return (
      <div className={wrapperClassName} aria-hidden={decorative || undefined}>
        {/* next/image는 onError 후 대체 처리가 번거로워 일반 img로 처리 */}
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          ref={imgRef}
          src={src}
          alt={alt}
          loading="lazy"
          decoding="async"
          onError={() => setFailed(true)}
          onLoad={(e) => {
            if (isBlankImage(e.currentTarget)) setFailed(true)
          }}
          style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', objectFit: 'cover' }}
        />
      </div>
    )
  }

  return (
    <div
      className={`${wrapperClassName} ${fallbackClassName}`.trim()}
      role={decorative ? undefined : 'img'}
      aria-label={decorative ? undefined : alt}
      aria-hidden={decorative || undefined}
    >
      {fallback}
    </div>
  )
}
