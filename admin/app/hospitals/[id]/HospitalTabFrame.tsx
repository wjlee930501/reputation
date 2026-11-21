'use client'

import type { ReactNode } from 'react'
import { OperatorIssuePanel } from '@/app/_components/OperatorIssuePanel'
import { safeOperatorError } from '@/lib/operations-journey'
import type { Hospital } from '@/types'
import { useHospitalHeader } from './hospital-context'

/**
 * 병원 정보에서 나뉜 탭(공개 페이지·근거 자료·도메인·검색)의 공통 틀. 헤더가 받아 둔 병원을
 * 그대로 쓰고, 못 받았으면 병원 정보 화면과 같은 안내를 보인다(결정 대기 2번 A draft).
 */
export function HospitalTabFrame({
  title,
  description,
  children,
}: {
  title: string
  description: string
  children: (hospital: Hospital, refetch: () => Promise<void>) => ReactNode
}) {
  const { hospital, loading, refetch } = useHospitalHeader()

  if (loading && !hospital) {
    return <div className="p-4 text-slate-500 sm:p-6 lg:p-8">불러오는 중...</div>
  }
  if (!hospital) {
    return (
      <div className="p-4 sm:p-6 lg:p-8">
        <OperatorIssuePanel
          message={safeOperatorError('onboarding', '운영 화면을 다시 불러 병원 정보를 확인하세요.')}
          surface="onboarding"
          onRetry={() => void refetch()}
          retryLabel="병원 정보 다시 불러오기"
        />
      </div>
    )
  }
  return (
    <div className="space-y-6 p-4 sm:p-6 lg:p-8">
      <div>
        <h2 className="text-xl font-bold text-slate-900">{title}</h2>
        <p className="mt-1 text-sm text-slate-600">{description}</p>
      </div>
      {children(hospital, refetch)}
    </div>
  )
}
