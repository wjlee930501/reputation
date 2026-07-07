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
 * 원장 사진처럼 외부 자산이 비어있거나(1x1 placeholder) 로드 실패·지연될 수 있는 이미지를
 * 우아하게 처리한다. 핵심: 모노그램 fallback을 항상 언더레이로 깔고, 실제 이미지는 그 위에
 * 덮어씌운다. 따라서 이미지가 없거나 로딩 중이거나 깨져도 "빈 회색 박스"가 아니라
 * 품위 있는 모노그램이 보인다(anti-slop: 빈 박스 0개).
 */
export function ClinicAvatar({ src, alt, wrapperClassName, fallbackClassName = '', fallback }: Props) {
  const [failed, setFailed] = useState(!src)
  const [loaded, setLoaded] = useState(false)
  const decorative = alt === ''
  const showImage = !failed && Boolean(src)

  // 캐시된 이미지는 React가 onLoad를 붙이기 전에 이미 complete 상태일 수 있어
  // ref 콜백에서 마운트 시점에 한 번 더 검사한다.
  const imgRef = useCallback((node: HTMLImageElement | null) => {
    if (!node) return
    if (node.complete) {
      if (isBlankImage(node)) setFailed(true)
      else setLoaded(true)
    }
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
      {showImage && (
        // next/image는 onError 후 대체 처리가 번거로워 일반 img로 처리
        // eslint-disable-next-line @next/next/no-img-element
        <img
          ref={imgRef}
          src={src as string}
          alt=""
          aria-hidden="true"
          loading="lazy"
          decoding="async"
          onError={() => setFailed(true)}
          onLoad={(e) => {
            if (isBlankImage(e.currentTarget)) setFailed(true)
            else setLoaded(true)
          }}
          style={{
            position: 'absolute',
            inset: 0,
            width: '100%',
            height: '100%',
            objectFit: 'cover',
            // 로드 확인 전까지 투명 → 모노그램 언더레이가 보인다(빈/깨진 박스 방지).
            opacity: loaded ? 1 : 0,
            transition: 'opacity 200ms ease',
          }}
        />
      )}
    </div>
  )
}
