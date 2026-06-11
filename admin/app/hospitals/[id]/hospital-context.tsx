'use client'

import { createContext, useContext } from 'react'
import type { Hospital } from '@/types'

export interface HospitalHeaderContextValue {
  hospital: Hospital | null
  /**
   * 병원 헤더(이름·상태 배지·진행 점)를 다시 불러온다.
   * 도메인 검증, 스케줄 저장, 콘텐츠 발행 등 상태 플래그를 바꾸는 작업 후 호출.
   */
  refetch: () => Promise<void>
}

export const HospitalHeaderContext = createContext<HospitalHeaderContextValue>({
  hospital: null,
  refetch: async () => {},
})

export function useHospitalHeader(): HospitalHeaderContextValue {
  return useContext(HospitalHeaderContext)
}
