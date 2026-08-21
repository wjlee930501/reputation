'use client'

import Image from 'next/image'
import { useCallback, useEffect, useState, type ReactNode } from 'react'

import { isOffAllowlistExternalUrl } from '@/lib/image-policy'

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
  /** 실제 렌더 너비를 Next 이미지 최적화기에 전달한다. */
  sizes?: string
  priority?: boolean
}

/**
 * 원장 사진처럼 외부 자산이 비어있거나(1x1 placeholder) 로드 실패·지연될 수 있는 이미지를
 * 우아하게 처리한다. 핵심: 모노그램 fallback을 항상 언더레이로 깔고, 실제 이미지는 그 위에
 * 덮어씌운다. 따라서 이미지가 없거나 로딩 중이거나 깨져도 "빈 회색 박스"가 아니라
 * 품위 있는 모노그램이 보인다(anti-slop: 빈 박스 0개).
 */
export function ClinicAvatar({
  src,
  alt,
  wrapperClassName,
  fallbackClassName = '',
  fallback,
  sizes = '(max-width: 720px) 100vw, 420px',
  priority = false,
}: Props) {
  const [failed, setFailed] = useState(!src)
  // 이미지는 클라이언트 마운트 후에만 렌더한다. SSR HTML에 들어간 img가 하이드레이션 전에
  // 로드 실패하면 onError가 유실되어 "깨진 이미지 아이콘"이 남는데, 마운트 게이팅으로 이를
  // 원천 차단한다. 모노그램 언더레이는 항상 렌더되므로 빈 박스·레이아웃 시프트가 없다.
  const [mounted, setMounted] = useState(false)
  useEffect(() => setMounted(true), [])
  const decorative = alt === ''
  const showImage = mounted && !failed && Boolean(src)

  // 캐시 이미지는 onLoad가 이미 지나갔을 수 있어 ref 마운트 시점에 blank(1x1·404) 여부를
  // 한 번 더 검사한다.
  const imgRef = useCallback((node: HTMLImageElement | null) => {
    if (!node) return
    if (node.complete && isBlankImage(node)) setFailed(true)
  }, [])

  // fallback(모노그램)은 언제나 렌더 → 이미지가 로드되면 그 위를 덮는다.
  return (
    <div
      className={`${wrapperClassName} ${fallbackClassName}`.trim()}
      role={decorative ? undefined : 'img'}
      aria-label={decorative ? undefined : alt}
      aria-hidden={decorative || undefined}
    >
      {fallback}
      {showImage && src && (
        <Image
          ref={imgRef}
          src={src}
          alt=""
          aria-hidden="true"
          fill
          sizes={sizes}
          priority={priority}
          loading={priority ? 'eager' : 'lazy'}
          quality={84}
          unoptimized={isOffAllowlistExternalUrl(src)}
          onError={() => setFailed(true)}
          onLoad={(e) => {
            if (isBlankImage(e.currentTarget)) setFailed(true)
          }}
          style={{
            objectFit: 'cover',
          }}
        />
      )}
    </div>
  )
}
