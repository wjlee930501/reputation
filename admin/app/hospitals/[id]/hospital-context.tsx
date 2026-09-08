'use client'

import { createContext, useContext } from 'react'
import type { Hospital, HospitalOverview } from '@/types'

export interface HospitalHeaderContextValue {
  hospital: Hospital | null
  /**
   * 헤더가 이미 받아 둔 3상태·예외·이번 달 요약. 하위 화면이 같은 순간의 병원을
   * 보도록 이 값을 재사용한다. overview 호출만 실패하면 null이고 헤더는 계속 뜬다.
   */
  overview: HospitalOverview | null
  /**
   * true from mount until the layout's first fetch settles (success or failure).
   * Lets a page tell "context hasn't loaded yet" apart from "load failed / not
   * found" when it seeds its own state from `hospital` instead of re-fetching.
   */
  loading: boolean
  /**
   * 병원 헤더(이름·상태 배지·3상태)를 다시 불러온다.
   * 도메인 검증, 스케줄 저장, 콘텐츠 발행 등 상태 플래그를 바꾸는 작업 후 호출.
   */
  refetch: () => Promise<void>
}

export const HospitalHeaderContext = createContext<HospitalHeaderContextValue>({
  hospital: null,
  overview: null,
  loading: true,
  refetch: async () => {},
})

export function useHospitalHeader(): HospitalHeaderContextValue {
  return useContext(HospitalHeaderContext)
}
