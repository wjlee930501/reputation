'use client'

import { Suspense, useMemo } from 'react'

import { selectCurrentAction } from '@/lib/operations-center'
import { OperatorIssuePanel } from '@/app/_components/OperatorIssuePanel'
import { CostGuardPanel } from './CostGuardPanel'
import { CurrentActionStrip } from './CurrentActionStrip'
import { OperationDetail } from './OperationDetail'
import { OperationsFilters } from './OperationsFilters'
import { OperationsQueueList } from './OperationsQueue'
import { useOperationsCenter } from './useOperationsCenter'

function OperationsCenter() {
  const center = useOperationsCenter()
  // "지금 먼저 처리"는 지금 보고 있는 큐에서 고른다. overview 전체(네 큐 × 5건)에서
  // 고르면 다른 탭의 항목을 가리켜, 눌렀을 때 목록에 없는 줄로 이동한다(G-6).
  const currentAction = useMemo(
    () => selectCurrentAction(center.visibleItems.length > 0 ? center.visibleItems : center.page?.items ?? []),
    [center.page, center.visibleItems],
  )

  return (
    <div className="ops-page">
      <header className="ops-header">
        <div>
          <p className="admin-eyebrow">MotionLabs 고객 운영 현황</p>
          <h1 className="title2 mt-1 text-slate-950">운영 센터</h1>
        </div>
        <p className="ops-refresh-note">화면은 자동으로 최신 상태를 확인합니다.</p>
      </header>

      <CurrentActionStrip item={currentAction} onOpen={center.select} checkedAt={center.checkedAt} />
      <OperationsFilters query={center.query} overview={center.overview} onPatch={center.patchQuery} />

      <div className="mt-4 flex items-end justify-between gap-4">
        <div>
          <h2 id="ops-queue-heading" tabIndex={-1} className="text-lg font-bold text-slate-950">처리 목록</h2>
          <p className="ops-readable mt-0.5 text-sm text-slate-500">작업 한 건을 선택해 원인과 안전한 처리 순서를 확인하세요.</p>
        </div>
        {center.loading && center.page ? <span role="status" className="shrink-0 text-xs text-slate-500">최신 상태 확인 중…</span> : null}
      </div>

      {center.loadError && center.page ? (
        <div className="mt-3">
          <OperatorIssuePanel
            message={center.loadError}
            surface="operations"
            onRetry={() => void center.reload(false)}
            retryLabel="최신 운영 상태 다시 확인"
          />
        </div>
      ) : null}

      <div className="ops-center-layout mt-3">
        <OperationsQueueList
          data={center.page}
          visibleItems={center.visibleItems}
          loading={center.loading}
          error={center.loadError}
          selectedId={center.query.detail}
          onSelect={center.select}
          onRetryLoad={() => void center.reload(false)}
          onPage={(page) => center.patchQuery({ page })}
          searchTerm={center.query.q}
          checkedAt={center.checkedAt}
        />
        <OperationDetail
          detail={center.detail}
          fallback={center.selectedRow}
          busy={center.busy}
          error={center.actionError}
          permissionDenied={center.permissionDenied}
          onClose={center.close}
          onMutate={(mutation) => void center.mutate(mutation)}
          checkedAt={center.checkedAt}
        />
      </div>

      <CostGuardPanel canRaiseLimit={center.canRaiseLimit} />
    </div>
  )
}

export default function OperationsPage() {
  return <Suspense fallback={<div className="ops-page"><div className="ops-queue-state" role="status">운영 센터를 여는 중입니다.</div></div>}><OperationsCenter /></Suspense>
}
