'use client'

import { useState } from 'react'

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
  // 재검수·승인은 예외 카드 자체를 없앤다. 결과 문구를 카드 안에 두면 카드가 사라지는
  // 순간 함께 사라져, 운영자는 방금 요청이 어떻게 됐는지 알 수 없다.
  const [notice, setNotice] = useState<string | null>(null)

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
      {notice && (
        <p
          role="status"
          className="rounded-xl border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-800"
        >
          {notice}
        </p>
      )}
      <ExceptionCards
        exceptions={overview.exceptions}
        onDone={refetch}
        onNotice={setNotice}
      />
      <MonthSummary hospitalId={overview.hospital_id} month={overview.month} />
    </div>
  )
}
