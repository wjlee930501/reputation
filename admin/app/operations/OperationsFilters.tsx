'use client'

import type { OperationsOverviewResponse, OperationsQueueParam } from '@/types'
import {
  operationStatusLabel,
  type OperationsQuery,
  type OperationsQueryPatch,
} from '@/lib/operations-center'

const TABS: readonly { readonly value: OperationsQueueParam; readonly api: string; readonly label: string }[] = [
  { value: 'onboarding', api: 'ONBOARDING', label: '신규·온보딩' },
  { value: 'today', api: 'TODAY', label: '오늘의 운영' },
  { value: 'reports', api: 'REPORTS', label: '월간 리포트' },
  { value: 'incidents', api: 'INCIDENTS', label: '문제·복구' },
]

const STATUS_VALUES = [
  'ONBOARDING', 'ANALYZING', 'BUILDING', 'PENDING_DOMAIN', 'ACTIVE', 'PAUSED',
  'PUBLISH_DUE', 'REVIEW_PENDING', 'OVERDUE_REVIEW', 'MISSING', 'COVERAGE_INCOMPLETE',
  'MANIFEST_MISMATCH', 'MANIFEST_OPEN', 'DOCTOR_ARTIFACT_MISSING',
  'DOCTOR_ARTIFACT_INVALID', 'REPORT_BLOCKED', 'DELIVERY_PENDING',
  'OPEN', 'RETRYING', 'RECOVERED', 'ACKNOWLEDGED',
] as const

type Props = {
  readonly query: OperationsQuery
  readonly overview: OperationsOverviewResponse | null
  readonly onPatch: (patch: OperationsQueryPatch) => void
}

export function OperationsFilters({ query, overview, onPatch }: Props) {
  return (
    <div className="mt-4">
      <nav className="ops-tabs" aria-label="운영 작업 구분">
        {TABS.map((tab) => {
          const summary = overview?.queues.find((item) => item.queue === tab.api)
          const selected = query.queue === tab.value
          return (
            <button
              key={tab.value}
              type="button"
              aria-current={selected ? 'page' : undefined}
              onClick={() => onPatch({ queue: tab.value })}
              className="ops-tab ops-control"
            >
              <span>{tab.label}</span>
              <span className="ops-tab-count" aria-label={`${summary?.total ?? 0}건`}>{summary?.total ?? 0}</span>
            </button>
          )
        })}
      </nav>

      {query.queue === 'incidents' ? (
        <nav className="mt-3 flex gap-2" aria-label="문제 복구 상태">
          <button
            type="button"
            aria-current={query.recovery === 'active' ? 'page' : undefined}
            onClick={() => onPatch({ recovery: 'active', status: null })}
            className={`ops-control rounded-full border px-4 text-sm font-semibold ${query.recovery === 'active' ? 'border-blue-600 bg-blue-50 text-blue-800' : 'border-slate-300 bg-white text-slate-600'}`}
          >
            조치 필요
          </button>
          <button
            type="button"
            aria-current={query.recovery === 'confirmed' ? 'page' : undefined}
            onClick={() => onPatch({ recovery: 'confirmed', status: null })}
            className={`ops-control rounded-full border px-4 text-sm font-semibold ${query.recovery === 'confirmed' ? 'border-emerald-600 bg-emerald-50 text-emerald-800' : 'border-slate-300 bg-white text-slate-600'}`}
          >
            복구 확인됨
          </button>
        </nav>
      ) : null}

      <details className="ops-filter-panel mt-3">
        <summary className="ops-control">필터 · 현재 화면에만 적용</summary>
        <div className="ops-filter-grid">
          <label>
            <span>담당자</span>
            <input value={query.owner} onChange={(event) => onPatch({ owner: event.target.value })} placeholder="이름 또는 이메일" />
          </label>
          <label>
            <span>상태</span>
            <select value={query.status} onChange={(event) => onPatch({ status: event.target.value })}>
              <option value="">전체</option>
              {STATUS_VALUES.map((status) => <option key={status} value={status}>{operationStatusLabel(status)}</option>)}
            </select>
          </label>
          <label>
            <span>심각도</span>
            <select value={query.severity} onChange={(event) => onPatch({ severity: event.target.value })}>
              <option value="">전체</option><option value="CRITICAL">긴급</option><option value="HIGH">높음</option><option value="MEDIUM">보통</option><option value="LOW">낮음</option>
            </select>
          </label>
          <label>
            <span>처리 기한</span>
            <select value={query.sla} onChange={(event) => onPatch({ sla: event.target.value })}>
              <option value="">전체</option><option value="OVERDUE">처리 기한 지남</option><option value="DUE">처리 기한 남음</option><option value="NONE">처리 기한 없음</option>
            </select>
          </label>
          <label className="ops-filter-search">
            <span>현재 페이지에서 찾기</span>
            <input value={query.q} onChange={(event) => onPatch({ q: event.target.value })} placeholder="병원명·할 일·영향" />
          </label>
          <button type="button" onClick={() => onPatch({ owner: null, status: null, severity: null, sla: null, q: null })} className="ops-control self-end rounded-lg border border-slate-300 px-3 text-sm font-semibold text-slate-600">필터 초기화</button>
        </div>
        <p className="px-4 pb-3 text-xs leading-5 text-slate-500">이 설정은 URL에만 유지되며 서버에 저장된 필터가 아닙니다.</p>
      </details>
    </div>
  )
}
