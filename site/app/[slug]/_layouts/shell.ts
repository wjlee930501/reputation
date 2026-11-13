import type { ClinicLayout } from '@/lib/clinic-layout'

import layoutA from './layout-a.module.css'
import layoutB from './layout-b.module.css'
import layoutC from './layout-c.module.css'

const SHELL_CLASSES: Record<ClinicLayout, string> = {
  A: layoutA.shell,
  B: layoutB.shell,
  C: layoutC.shell,
}

/**
 * `clinic-shell`에 덧붙일 레이아웃 클래스. 홈과 하위 페이지(의료진·진료·건강 정보·오시는 길)가
 * 같은 값을 써서 머리·꼬리·서체·색이 레이아웃을 따른다(2026-11-13 결정: 하위 페이지 본문 구조는
 * 공통). 레이아웃을 고르지 않은 병원은 빈 문자열 — 지금 화면 그대로다.
 */
export function clinicLayoutShellClass(layout: ClinicLayout | null): string {
  return layout ? `clinic-shell--layout-${layout.toLowerCase()} ${SHELL_CLASSES[layout]}` : ''
}
