'use client'

import Image from 'next/image'
import { useCallback, useState } from 'react'

import { ContentMotif } from '@/components/brand'
import { isOffAllowlistExternalUrl } from '@/lib/image-policy'

interface Props {
  type: string
  /** 해석된 이미지 URL (없으면 유형 모티프만 노출) */
  src?: string | null
  /** 이미지가 콘텐츠 정보를 전달할 때 쓰는 대체 텍스트. 카드 장식 이미지는 생략한다. */
  alt?: string
  /** 시각 변주 — featured(대형) / card(중형) / band(가로 배너) */
  variant?: 'featured' | 'card' | 'band'
  className?: string
}

function isBlankImage(img: HTMLImageElement): boolean {
  return (img.naturalWidth || 0) <= 2
}

const COVER_SIZES: Record<NonNullable<Props['variant']>, string> = {
  featured: '(max-width: 920px) 100vw, 58vw',
  card: '(max-width: 720px) 100vw, (max-width: 1080px) 50vw, 360px',
  band: '(max-width: 720px) 100vw, 720px',
}

/**
 * 콘텐츠 커버 — 유형별 추상 모티프를 항상 언더레이로 깔고, 실제 이미지가 있으면 그 위에
 * 덮어씌운다(로드 확인 후 페이드인). 이미지가 없거나 404/blank여도 "빈 회색 박스"가 아니라
 * 유형 모티프가 보인다(anti-slop: 빈 박스 0개). 색상은 유형 태그 클래스로 스코프된다.
 */
export function ContentCover({ type, src, alt = '', variant = 'card', className = '' }: Props) {
  const [failed, setFailed] = useState(!src)
  // SSR에서도 대표 이미지가 HTML에 포함되어 크롤러가 본문과 함께 발견할 수 있어야 한다.
  // 로드 실패·빈 placeholder는 하이드레이션 뒤 모티프로 대체한다.
  const showImage = !failed && Boolean(src)

  // 유형 모티프를 항상 언더레이로 깔고 이미지를 그 위에 덮는다. 실패(404)·빈 placeholder(1x1)
  // 일 때만 이미지를 제거한다(빈 회색 박스 0개).
  const imgRef = useCallback((node: HTMLImageElement | null) => {
    if (!node) return
    if (node.complete && isBlankImage(node)) setFailed(true)
  }, [])

  return (
    <div
      className={`clinic-cover clinic-cover--${variant} clinic-cover--${(type || 'FAQ').toLowerCase()} ${className}`.trim()}
      aria-hidden={alt ? undefined : true}
    >
      {!showImage && (
        <>
          <span className="clinic-cover-watermark">
            <ContentMotif type={type} />
          </span>
          <span className="clinic-cover-motif">
            <ContentMotif type={type} />
          </span>
        </>
      )}
      {showImage && src && (
        <Image
          ref={imgRef}
          src={src}
          alt={alt}
          fill
          sizes={COVER_SIZES[variant]}
          priority={variant === 'featured'}
          loading={variant === 'featured' ? 'eager' : 'lazy'}
          quality={84}
          unoptimized={isOffAllowlistExternalUrl(src)}
          onError={() => setFailed(true)}
          onLoad={(e) => {
            if (isBlankImage(e.currentTarget)) setFailed(true)
          }}
          className="clinic-cover-img"
        />
      )}
    </div>
  )
}
