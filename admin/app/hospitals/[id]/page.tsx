'use client'

import { useHospitalHeader } from './hospital-context'
import { ExceptionCards } from './status/ExceptionCards'
import { MonthSummary } from './status/MonthSummary'
import { StatusCards } from './status/StatusCards'

/**
 * 병원 현황 한 장.
 *
 * 세 상태·예외·이번 달 요약은 헤더가 이미 받아 둔 `overview` 한 응답에서 온다.
 * 화면이 조각마다 따로 부르면 카드마다 다른 순간의 병원을 보여 준다.
 */
export default function HospitalStatusPage() {
  const { overview, loading, refetch } = useHospitalHeader()

  if (!overview) {
    return (
      <div className="p-4 text-sm text-slate-500 sm:p-6 lg:p-8">
        {loading ? (
          '불러오는 중...'
        ) : (
          <span className="inline-flex flex-wrap items-center gap-3">
            현황을 불러오지 못했습니다
            <button
              type="button"
              onClick={() => void refetch()}
              className="inline-flex min-h-11 items-center rounded-lg border border-slate-300 bg-white px-3 text-xs font-medium text-slate-700 hover:bg-slate-50"
            >
              다시 시도
            </button>
          </span>
        )}
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-[1100px] space-y-5 p-4 sm:p-6 lg:p-8">
      <StatusCards overview={overview} />
      <ExceptionCards exceptions={overview.exceptions} onDone={refetch} />
      <MonthSummary hospitalId={overview.hospital_id} month={overview.month} />
    </div>
  )
}
