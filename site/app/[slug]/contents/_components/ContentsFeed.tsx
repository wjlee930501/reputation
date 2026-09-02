'use client'

import { useSearchParams } from 'next/navigation'

import type { ContentSummary } from '@/lib/api'

import { ContentsFeedView } from './ContentsFeedView'

export interface ContentsFeedProps {
  contents: ContentSummary[]
  hospitalRootUrl: string
  directorName: string
}

/** `?type=` 필터를 클라이언트에서 읽어 적용한다 — 서버 컴포넌트는 searchParams를 읽지
 * 않으므로 이 페이지는 쿼리와 무관하게 정적/ISR로 유지된다(부모의 Suspense가 정적 폴백을
 * 감싼다). useSearchParams는 이 컴포넌트를 클라이언트 전용으로 만들 뿐, 라우트 전체를
 * dynamic으로 만들지 않는다. */
export function ContentsFeed({ contents, hospitalRootUrl, directorName }: ContentsFeedProps) {
  const searchParams = useSearchParams()
  const activeType = searchParams.get('type')

  return (
    <ContentsFeedView
      contents={contents}
      hospitalRootUrl={hospitalRootUrl}
      directorName={directorName}
      activeType={activeType}
    />
  )
}
