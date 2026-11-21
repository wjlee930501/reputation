import type { ReactNode } from 'react'

import type { SiteSectionAlign, SiteSectionBackground } from '@/lib/clinic-site-config'

/**
 * 섹션 설정이 바꾼 섹션 모양(③-2c — 제목 정렬·배경)을 덧입히는 틀. 바꾼 것이 없으면 틀 없이 지금 마크업
 * 그대로 그린다. 정렬은 섹션 머리(제목·안내 문장·소제목)에만 걸리고 본문 배치는 그대로다(globals.css `.clinic-look`).
 * 정렬만 바꾼 틀은 상자를 만들지 않고(`display: contents`), 배경을 바꾼 틀은 화면 폭 상자로 배경을 칠한다 —
 * 감싼 섹션의 자기 배경은 걷는다.
 *
 * `nested`는 다른 섹션 안에 그려지는 섹션(C 최근 소식·진료시간·진료 과정, A 질문 목록 뒤 FAQ)이다. 바깥 섹션의
 * 정렬은 이 틀 안으로 들어오지 않고, 이 틀은 자기 정렬만 건다. 바깥 섹션이 정렬을 바꿨으면(`force`) 자기 정렬이
 * 없어도 경계로 틀을 둔다 — 지금 모양 그대로 남게.
 */
export function SectionLook({
  align,
  background = null,
  children,
  nested = false,
  force = false,
}: {
  align: SiteSectionAlign | null
  background?: SiteSectionBackground | null
  children: ReactNode
  nested?: boolean
  force?: boolean
}) {
  if (!children) return null
  if (!align && !background && !force) return <>{children}</>
  const className = ['clinic-look', nested ? 'clinic-look-nested' : '', background ? 'clinic-look-bg' : '']
    .filter(Boolean)
    .join(' ')
  return (
    <div className={className} data-look-align={align ?? undefined} data-look-bg={background ?? undefined}>
      {children}
    </div>
  )
}
