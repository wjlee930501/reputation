'use client'

import { useCallback, useEffect, useState } from 'react'

import { ContentMotif } from './brand'

interface Props {
  type: string
  /** 해석된 이미지 URL (없으면 유형 모티프만 노출) */
  src?: string | null
  /** 시각 변주 — featured(대형) / card(중형) / band(가로 배너) */
  variant?: 'featured' | 'card' | 'band'
  className?: string
}

function isBlankImage(img: HTMLImageElement): boolean {
  return (img.naturalWidth || 0) <= 2
}

/**
 * 콘텐츠 커버 — 유형별 추상 모티프를 항상 언더레이로 깔고, 실제 이미지가 있으면 그 위에
 * 덮어씌운다(로드 확인 후 페이드인). 이미지가 없거나 404/blank여도 "빈 회색 박스"가 아니라
 * 유형 모티프가 보인다(anti-slop: 빈 박스 0개). 색상은 유형 태그 클래스로 스코프된다.
 */
export function ContentCover({ type, src, variant = 'card', className = '' }: Props) {
  const [failed, setFailed] = useState(!src)
  // 이미지는 클라이언트 마운트 후에만 렌더 — SSR img가 하이드레이션 전 실패 시 onError 유실로
  // 깨진 아이콘이 남는 것을 차단한다. 유형 모티프 언더레이는 항상 렌더되어 빈 박스가 없다.
  const [mounted, setMounted] = useState(false)
  useEffect(() => setMounted(true), [])
  const showImage = mounted && !failed && Boolean(src)

  // 유형 모티프를 항상 언더레이로 깔고 이미지를 그 위에 덮는다. 실패(404)·빈 placeholder(1x1)
  // 일 때만 이미지를 제거한다(빈 회색 박스 0개).
  const imgRef = useCallback((node: HTMLImageElement | null) => {
    if (!node) return
    if (node.complete && isBlankImage(node)) setFailed(true)
  }, [])

  return (
    <div
      className={`clinic-cover clinic-cover--${variant} clinic-cover--${(type || 'FAQ').toLowerCase()} ${className}`.trim()}
      aria-hidden="true"
    >
      <span className="clinic-cover-watermark">
        <ContentMotif type={type} />
      </span>
      <span className="clinic-cover-motif">
        <ContentMotif type={type} />
      </span>
      {showImage && (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          ref={imgRef}
          src={src as string}
          alt=""
          decoding="async"
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
