import type { ContentSummary, Hospital } from '@/lib/api'
import type { ClinicAccessMode, ClinicComposition, ClinicContentDensity, ClinicMediaMode } from '@/lib/clinic-design'
import type { ClinicPhysician } from '@/lib/clinic-physicians'
import type { FaqEntry } from '@/lib/schema'

/**
 * 레이아웃 A/C가 받는 화면 입력. 페이지(`page.tsx`)가 한 번만 만든다.
 *
 * FAQ 항목과 질문 목록 후보는 페이지가 고른 값 그대로다 — 구조화 데이터(FAQPage)와
 * 화면이 같은 항목을 말하고, 두 질문 표면이 같은 글을 반복하지 않게 하는 계약이
 * 레이아웃마다 다시 계산되며 어긋나지 않게 한다.
 */
export interface ClinicHomeModel {
  hospital: Hospital
  hospitalRootUrl: string
  physicians: ClinicPhysician[]
  /** `selectFaqEntries()` 결과 — FAQPage JSON-LD의 mainEntity와 같다. */
  faqEntries: FaqEntry[]
  /** FAQ 섹션이 가져간 글을 뺀 나머지 — 질문 목록은 여기서만 고른다. */
  clusterContents: ContentSummary[]
  contents: ContentSummary[]
  contentDensity: ClinicContentDensity
  composition: ClinicComposition
  heroPhotoUrl: string | null
  mediaMode: ClinicMediaMode
  accessMode: ClinicAccessMode
  /** 공식 채널 — 스키마 sameAs·llms.txt와 같은 값. */
  factLinks: Array<{ url: string | null; label: string }>
}
