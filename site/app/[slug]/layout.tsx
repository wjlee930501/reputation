import type { Metadata } from 'next'
import type { ReactNode } from 'react'

import { fetchHospital } from '@/lib/api'
import { clinicFaviconHref } from '@/lib/clinic-favicon'

type Props = {
  children: ReactNode
  params: Promise<{ slug: string }>
}

// 병원 페이지 전체의 탭 아이콘을 병원 모노그램으로 바꾼다. `icons`를 지정하면 루트의
// Re:putation 아이콘(app/favicon.ico·icon.png·apple-icon.png)을 물려받지 않는다.
// 병원 조회가 실패해도 플랫폼 심볼로 돌아가지 않도록 같은 라우트의 중립 아이콘을 건다.
export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { slug } = await params
  try {
    const hospital = await fetchHospital(slug)
    return {
      icons: {
        icon: [{ url: clinicFaviconHref(slug, hospital, 'icon'), type: 'image/png', sizes: '96x96' }],
        apple: [{ url: clinicFaviconHref(slug, hospital, 'apple'), sizes: '180x180' }],
      },
    }
  } catch {
    const neutral = `/favicon/${encodeURIComponent(slug)}`
    return {
      icons: {
        icon: [{ url: neutral, type: 'image/png', sizes: '96x96' }],
        apple: [{ url: `${neutral}?variant=apple`, sizes: '180x180' }],
      },
    }
  }
}

export default function ClinicLayout({ children }: Props) {
  return children
}
