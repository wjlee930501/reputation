import type { ReactNode } from 'react'

// 병원 공개 표면의 스타일 레이어. 토큰 → 뼈대 → 섹션 순서로 읽히며, 루트 레이아웃의
// globals.css 뒤에 오므로 같은 특정도에서 이 파일들이 이긴다. 한 컴포넌트의 규칙은
// 한 파일에만 있다 — 다른 파일에서 같은 셀렉터를 다시 쓰지 않는다.
import './_styles/tokens.css'
import './_styles/base.css'
import './_styles/header.css'
import './_styles/hero.css'
import './_styles/treatments.css'
import './_styles/doctor.css'
import './_styles/principles.css'
import './_styles/content.css'
import './_styles/flow.css'
import './_styles/gallery.css'
import './_styles/facts.css'
import './_styles/footer.css'

export default function ClinicLayout({ children }: { children: ReactNode }) {
  return children
}
