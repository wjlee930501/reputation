import type { ReactNode } from 'react'

import type { SiteSectionAlign } from '@/lib/clinic-site-config'

/**
 * 섹션 설정이 바꾼 섹션 머리 모양(③-2c — 제목 정렬)을 덧입히는 틀. 바꾼 것이 없으면 틀 없이 지금 마크업
 * 그대로 그린다. 정렬은 섹션 머리(제목·안내 문장·소제목)에만 걸리고 본문 배치는 그대로다(globals.css `.clinic-look`).
 *
 * `nested`는 다른 섹션 안에 그려지는 섹션(C 최근 소식·진료시간·진료 과정, A 질문 목록 뒤 FAQ)이다. 바깥 섹션의
 * 정렬은 이 틀 안으로 들어오지 않고, 이 틀은 자기 정렬만 건다. 바깥 섹션이 정렬을 바꿨으면(`force`) 자기 정렬이
 * 없어도 경계로 틀을 둔다 — 지금 모양 그대로 남게.
 */
export function SectionLook({
  align,
  children,
  nested = false,
  force = false,
}: {
  align: SiteSectionAlign | null
  children: ReactNode
  nested?: boolean
  force?: boolean
}) {
  if (!children) return null
  if (!align && !force) return <>{children}</>
  return (
    <div className={nested ? 'clinic-look clinic-look-nested' : 'clinic-look'} data-look-align={align ?? undefined}>
      {children}
    </div>
  )
}
