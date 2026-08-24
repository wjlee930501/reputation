'use client'

import { useState } from 'react'

import type { OperationsQueueResponse, OperationsQueueRow } from '@/types'
import { fetchAPI } from '@/lib/api'
import {
  describeOperationsDeadline,
  operationStatusLabel,
  operationsRowTitle,
  safeCauseText,
} from '@/lib/operations-center'
import { OperatorIssuePanel } from '@/app/_components/OperatorIssuePanel'

type QueueProps = {
  readonly data: OperationsQueueResponse | null
  readonly visibleItems: readonly OperationsQueueRow[]
  readonly loading: boolean
  readonly error: string
  readonly selectedId: string
  readonly onSelect: (item: OperationsQueueRow) => void
  readonly onRetryLoad: () => void
  readonly onPage: (page: number) => void
  /** 이 화면에서만 걸리는 검색어 — 서버가 센 탭 숫자와 목록이 어긋나는 이유다 */
  readonly searchTerm: string
  /** 남은 처리 기한 계산의 기준 시각 — 목록을 받은 시점 */
  readonly checkedAt: number
  readonly canRaiseLimit: boolean
}

const SEVERITY_LABELS = {
  LOW: '낮음',
  MEDIUM: '보통',
  HIGH: '높음',
  CRITICAL: '긴급',
} as const

function formatDate(value: string | null): string {
  if (!value) return '기록 없음'
  return new Intl.DateTimeFormat('ko-KR', {
    month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit',
  }).format(new Date(value))
}

const COST_CATEGORY_LABELS: Record<string, string> = {
  content: '콘텐츠 생성',
  image: '이미지 생성',
  sov: 'AI 답변 언급률 측정',
  leadgen: '무료 진단 측정',
}

type CostStatus = {
  readonly categories: readonly {
    readonly category: string
    readonly daily_limit: number
    readonly daily_limit_default: number
  }[]
}

function CostCauseAction({ item, canRaiseLimit }: {
  readonly item: OperationsQueueRow
  readonly canRaiseLimit: boolean
}) {
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')
  const category = item.cost_guard_category
  if (!category) return null
  if (!canRaiseLimit) {
    return <p className="mt-2 text-xs text-slate-500">한도 상향은 계정 소유자에게 요청하세요.</p>
  }

  async function raiseLimit() {
    if (!category || reason.trim().length < 3) return
    setBusy(true)
    setNotice('')
    setError('')
    try {
      const status = await fetchAPI<CostStatus>('/admin/operations/cost-guard')
      const usage = status.categories.find((entry) => entry.category === category)
      if (!usage || usage.daily_limit_default <= 0) {
        setError('이 항목은 일일 한도를 올릴 수 없습니다.')
        return
      }
      const doubled = usage.daily_limit_default * 2
      if (usage.daily_limit >= doubled) {
        setNotice('오늘 한도는 이미 2배로 적용되어 있습니다.')
        return
      }
      await fetchAPI('/admin/operations/cost-guard/daily-limit', {
        method: 'POST',
        body: JSON.stringify({ category, limit: doubled, reason: reason.trim() }),
      })
      setNotice('오늘 한도를 2배로 올렸습니다. 차단된 작업을 다시 시도해 주세요.')
      setReason('')
    } catch {
      setError('한도를 올리지 못했습니다. 비용 안전장치에서 상태를 다시 확인해 주세요.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mt-3 rounded-lg border border-amber-200 bg-amber-50 p-2.5">
      <label className="block text-xs font-semibold text-amber-950">
        오늘 한도 2배 · {COST_CATEGORY_LABELS[category] ?? category}
        <input value={reason} onChange={(event) => setReason(event.target.value)} maxLength={200} placeholder="상향 사유 (3자 이상)" className="mt-1 w-full rounded-md border border-amber-300 bg-white px-2 py-1.5 font-normal" />
      </label>
      <button type="button" disabled={busy || reason.trim().length < 3} onClick={() => void raiseLimit()} className="ops-control mt-2 w-full rounded-md bg-amber-800 px-3 text-xs font-bold text-white disabled:opacity-45">{busy ? '적용 중…' : '오늘 한도 2배 적용'}</button>
      {notice ? <p role="status" className="mt-1.5 text-xs text-emerald-800">{notice}</p> : null}
      {error ? <p role="alert" className="mt-1.5 text-xs text-red-700">{error}</p> : null}
    </div>
  )
}

function ProblemBlock({ item, canRaiseLimit }: { readonly item: OperationsQueueRow; readonly canRaiseLimit: boolean }) {
  const urgent = item.sla_state === 'OVERDUE' || item.severity === 'CRITICAL'
  const costTitle = item.cost_guard_category
    ? `${COST_CATEGORY_LABELS[item.cost_guard_category] ?? item.cost_guard_category} 일일 한도 소진`
    : null
  return (
    <div className="min-w-0">
      {/* 같은 병원의 여러 줄이 제목만으로 구분되도록 무슨 일인지 함께 적는다(G-4). */}
      <p className="ops-readable font-semibold text-slate-900">{costTitle ?? operationsRowTitle(item)}</p>
      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        <span className={`ops-badge ${urgent ? 'ops-badge--danger' : 'ops-badge--neutral'}`}>
          {SEVERITY_LABELS[item.severity]}
        </span>
        <span className="ops-badge ops-badge--neutral">{operationStatusLabel(item.status)}</span>
        {item.same_type_count > 1 ? (
          <span className="ops-badge border-blue-200 bg-blue-50 text-blue-800">
            같은 원인 {item.same_type_count}건 · 영향 병원 {item.affected_hospital_count}곳
          </span>
        ) : null}
      </div>
      {/* 원인은 원인이 있는 행에만 붙인다.
          온보딩·오늘 발행·리포트 큐는 사건이 아니라 예정된 일감이라 safe_cause가 없다.
          그런데 이 자리를 늘 채우면 그 행들까지 `원인 설명을 확인할 수 없습니다`가 붙어,
          아무 문제 없는 줄이 전부 장애처럼 읽히고 조치 대신 개발팀 문의를 가리켰다(G-1). */}
      {item.safe_cause && (
        <p className="ops-readable mt-1.5 text-sm leading-5 text-slate-600">
          {safeCauseText(item.safe_cause)}
        </p>
      )}
      <CostCauseAction item={item} canRaiseLimit={canRaiseLimit} />
    </div>
  )
}

function ImpactBlock({ item }: { readonly item: OperationsQueueRow }) {
  return <p className="ops-readable text-sm leading-5 text-slate-700">{item.impact}</p>
}

function ActionBlock({ item, now }: { readonly item: OperationsQueueRow; readonly now: number }) {
  const deadline = describeOperationsDeadline(item, now, formatDate)
  return (
    <div className="min-w-0 text-sm">
      <p className="ops-readable leading-5 text-slate-700">{item.next_action}</p>
      <p className="mt-2 font-semibold text-slate-800">담당 · {item.owner?.name ?? '미지정'}</p>
      <p
        className={
          deadline.tone === 'overdue'
            ? 'mt-1 font-semibold text-red-700'
            : deadline.tone === 'due_soon'
              ? 'mt-1 font-semibold text-amber-700'
              : 'mt-1 text-slate-500'
        }
      >
        {deadline.text}
      </p>
    </div>
  )
}

function SelectButton({ item, selectedId, onSelect }: {
  readonly item: OperationsQueueRow
  readonly selectedId: string
  readonly onSelect: (item: OperationsQueueRow) => void
}) {
  return (
    <button
      type="button"
      onClick={() => onSelect(item)}
      aria-pressed={selectedId === item.id}
      className="ops-control w-full rounded-lg border border-slate-300 bg-white px-3 text-sm font-semibold text-slate-700 hover:border-blue-400 hover:text-blue-700"
    >
      {selectedId === item.id ? '선택됨' : '상세 보기'}
    </button>
  )
}

export function OperationsQueueList(props: QueueProps) {
  const { data, visibleItems, loading, error, selectedId, onSelect, onRetryLoad, onPage, searchTerm, checkedAt, canRaiseLimit } = props
  if (loading && data === null) {
    return <div className="ops-queue-state" role="status">운영 목록을 불러오는 중입니다.</div>
  }
  if (error && data === null) {
    return (
      <div className="ops-queue-state">
        <OperatorIssuePanel
          message={error}
          surface="operations"
          onRetry={onRetryLoad}
          retryLabel="운영 목록 다시 불러오기"
        />
      </div>
    )
  }
  if (visibleItems.length === 0) {
    // 탭 숫자는 서버가 센 큐 전체이고 검색은 이 화면에서만 걸린다. 검색 때문에 비었는데
    // "처리할 일이 없습니다"라고 하면 탭 숫자와 정면으로 어긋난다(G-5).
    const hiddenBySearch = searchTerm.length > 0 && (data?.items.length ?? 0) > 0
    return (
      <div className="ops-queue-state">
        <p className="font-semibold text-slate-800">
          {hiddenBySearch
            ? `“${searchTerm}”과 맞는 항목이 이 페이지에 없습니다.`
            : '지금 이 조건에서 처리할 일이 없습니다.'}
        </p>
        <p className="mt-1 text-sm text-slate-500">
          {hiddenBySearch
            ? `이 페이지에는 ${data?.items.length ?? 0}건이 있고, 이 큐 전체는 ${data?.total ?? 0}건입니다. 검색어를 지우면 다시 보입니다.`
            : '필터를 바꾸거나 잠시 후 새로고침해 주세요.'}
        </p>
      </div>
    )
  }

  return (
    <section aria-labelledby="ops-queue-heading" className="min-w-0">
      <div className="ops-table-wrap rounded-xl border border-slate-200 bg-white">
        <table className="ops-table w-full table-fixed">
          <thead><tr><th>병원 · 무슨 문제인지</th><th>고객 영향</th><th>지금 할 일 · 처리 기한</th><th>처리</th></tr></thead>
          <tbody>
            {visibleItems.map((item) => (
              <tr key={item.id} data-selected={selectedId === item.id}>
                <td><ProblemBlock item={item} canRaiseLimit={canRaiseLimit} /></td>
                <td><ImpactBlock item={item} /></td>
                <td><ActionBlock item={item} now={checkedAt} /></td>
                <td><SelectButton item={item} selectedId={selectedId} onSelect={onSelect} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="ops-cards" aria-label="운영 작업 카드 목록">
        {visibleItems.map((item) => (
          <article key={item.id} className="rounded-xl border border-slate-200 bg-white p-4">
            <ProblemBlock item={item} canRaiseLimit={canRaiseLimit} />
            <div className="mt-3 border-t border-slate-100 pt-3"><p className="text-xs font-bold text-slate-500">고객 영향</p><ImpactBlock item={item} /></div>
            <div className="mt-3 border-t border-slate-100 pt-3"><p className="text-xs font-bold text-slate-500">지금 할 일</p><ActionBlock item={item} now={checkedAt} /></div>
            <div className="mt-3"><SelectButton item={item} selectedId={selectedId} onSelect={onSelect} /></div>
          </article>
        ))}
      </div>
      {data && data.total > data.page_size ? (
        <nav aria-label="운영 목록 페이지" className="mt-4 flex items-center justify-between gap-3">
          <button type="button" disabled={data.page <= 1} onClick={() => onPage(data.page - 1)} className="ops-control rounded-lg border border-slate-300 px-4 text-sm disabled:opacity-40">이전</button>
          <span className="text-sm tabular-nums text-slate-500">{data.page} / {Math.ceil(data.total / data.page_size)}</span>
          <button type="button" disabled={data.page * data.page_size >= data.total} onClick={() => onPage(data.page + 1)} className="ops-control rounded-lg border border-slate-300 px-4 text-sm disabled:opacity-40">다음</button>
        </nav>
      ) : null}
    </section>
  )
}
