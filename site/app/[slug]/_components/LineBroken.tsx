import { Fragment, type ReactNode } from 'react'

import { lineBreakPieces, type LineBreakDevice } from '@/lib/clinic-line-breaks'

/**
 * 섹션 설정의 줄바꿈 지정을 글에 끼운다(③-2f). 글은 한 번만 그리고, 그 기기에서만 보이는 `<br>`을 둔다
 * (`clinic-br--pc`는 휴대폰에서, `clinic-br--mobile`은 넓은 화면에서 감춘다 — globals.css). 이 글 구간(`start`부터)에
 * 줄바꿈이 없으면 `fallback`(지금 마크업)을 그린다.
 */
export function LineBroken({
  text,
  breaks,
  start = 0,
  breakAtStart = false,
  fallback,
}: {
  text: string
  breaks: Map<number, LineBreakDevice>
  start?: number
  /** 앞 조각과 같은 줄에 이어지는 조각 — 시작 자리의 줄바꿈도 넣는다. */
  breakAtStart?: boolean
  fallback?: ReactNode
}) {
  const pieces = lineBreakPieces(text, start, breaks, breakAtStart)
  if (!pieces.some((piece) => piece.kind === 'br')) return <>{fallback ?? text}</>
  return (
    <>
      {pieces.map((piece, index) =>
        piece.kind === 'text' ? (
          <Fragment key={index}>{piece.text}</Fragment>
        ) : (
          <br key={index} className={piece.device === 'both' ? undefined : `clinic-br--${piece.device}`} />
        ),
      )}
    </>
  )
}
