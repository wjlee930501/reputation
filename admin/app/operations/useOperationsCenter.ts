'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'

import { ApiError, fetchAPI } from '@/lib/api'
import { fetchCurrentAccount } from '@/lib/current-account'
import { safeOperatorError } from '@/lib/operations-journey'
import {
  canonicalizeOperationsQuery,
  createUserActionKey,
  interpretOperationsConflict,
  readOperationsQuery,
  shouldPollRun,
  updateOperationsQuery,
  type OperationsQueryPatch,
} from '@/lib/operations-center'
import type {
  OperationsIncidentDetail,
  OperationsOverviewResponse,
  OperationsQueue,
  OperationsQueueParam,
  OperationsQueueResponse,
  OperationsQueueRow,
  OperationsRunSummary,
} from '@/types'
import type { OperationMutation } from './OperationDetail'

function apiQueue(queue: OperationsQueueParam): OperationsQueue {
  switch (queue) {
    case 'onboarding': return 'ONBOARDING'
    case 'today': return 'TODAY'
    case 'reports': return 'REPORTS'
    case 'incidents': return 'INCIDENTS'
  }
}

function paramQueue(queue: OperationsQueue): OperationsQueueParam {
  switch (queue) {
    case 'ONBOARDING': return 'onboarding'
    case 'TODAY': return 'today'
    case 'REPORTS': return 'reports'
    case 'INCIDENTS': return 'incidents'
  }
}

function errorMessage(error: unknown): string {
  const action = error instanceof ApiError && error.status === 403
    ? '권한 있는 담당자에게 요청하고, 처리할 수 없으면 개발팀 문의용 정보를 복사하세요.'
    : '운영 목록 다시 불러오기를 누르고, 계속 실패하면 개발팀 문의용 정보를 복사하세요.'
  return safeOperatorError('operations', action)
}

function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError'
}

export function useOperationsCenter() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const rawQuery = searchParams.toString()
  const query = useMemo(() => readOperationsQuery(new URLSearchParams(rawQuery)), [rawQuery])
  const [overview, setOverview] = useState<OperationsOverviewResponse | null>(null)
  const [page, setPage] = useState<OperationsQueueResponse | null>(null)
  const [checkedAt, setCheckedAt] = useState(() => Date.now())
  const [detail, setDetail] = useState<OperationsIncidentDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [actionError, setActionError] = useState('')
  const [permissionDenied, setPermissionDenied] = useState(false)
  const [busy, setBusy] = useState(false)
  const [canRaiseLimit, setCanRaiseLimit] = useState(false)
  const centerAbort = useRef<AbortController | null>(null)
  const detailAbort = useRef<AbortController | null>(null)

  const patchQuery = useCallback((patch: OperationsQueryPatch) => {
    const next = updateOperationsQuery(new URLSearchParams(rawQuery), patch)
    router.replace(`/operations?${next.toString()}`, { scroll: false })
  }, [rawQuery, router])

  useEffect(() => {
    const canonical = canonicalizeOperationsQuery(new URLSearchParams(rawQuery)).toString()
    if (canonical !== rawQuery) router.replace(`/operations?${canonical}`, { scroll: false })
  }, [rawQuery, router])

  useEffect(() => {
    void fetchCurrentAccount().then((account) => setCanRaiseLimit(account?.role === 'OWNER'))
  }, [])

  const loadCenter = useCallback(async (silent: boolean) => {
    centerAbort.current?.abort()
    const controller = new AbortController()
    centerAbort.current = controller
    if (!silent) setLoading(true)
    const filters = new URLSearchParams()
    if (query.owner) filters.set('owner', query.owner)
    if (query.status) filters.set('status', query.status)
    if (query.severity) filters.set('severity', query.severity)
    if (query.sla) filters.set('sla', query.sla)
    const queueFilters = new URLSearchParams(filters)
    if (query.queue === 'incidents') queueFilters.set('recovery', query.recovery)
    queueFilters.set('page', String(query.page))
    try {
      const [nextOverview, nextPage] = await Promise.all([
        fetchAPI<OperationsOverviewResponse>(`/admin/operations/overview?${filters}`, { signal: controller.signal }),
        fetchAPI<OperationsQueueResponse>(`/admin/operations/queues/${apiQueue(query.queue)}?${queueFilters}`, { signal: controller.signal }),
      ])
      setOverview(nextOverview)
      setPage(nextPage)
      // 남은 처리 기한은 이 응답을 받은 시각 기준이다. 렌더 중에 시계를 읽으면 같은
      // 목록이 리렌더마다 다른 남은 시간을 보여준다.
      setCheckedAt(Date.now())
      setLoadError('')
    } catch (error) {
      if (!isAbort(error)) setLoadError(errorMessage(error))
    } finally {
      if (!silent && !controller.signal.aborted) setLoading(false)
    }
  }, [query.owner, query.page, query.queue, query.recovery, query.severity, query.sla, query.status])

  useEffect(() => {
    void loadCenter(false)
    return () => centerAbort.current?.abort()
  }, [loadCenter])

  useEffect(() => {
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') void loadCenter(true)
    }, 12_000)
    return () => window.clearInterval(timer)
  }, [loadCenter])

  const selectedRow = useMemo(() => {
    if (!query.detail) return null
    return page?.items.find((item) => item.id === query.detail)
      ?? overview?.items.find((item) => item.id === query.detail)
      ?? null
  }, [overview, page, query.detail])

  const loadDetail = useCallback(async (row: OperationsQueueRow) => {
    detailAbort.current?.abort()
    const controller = new AbortController()
    detailAbort.current = controller
    if (row.operation_run_id && row.customer.hospital_id) {
      try {
        const run = await fetchAPI<OperationsRunSummary>(
          `/admin/operations/hospitals/${row.customer.hospital_id}/runs/${row.operation_run_id}`,
          { signal: controller.signal },
        )
        setDetail({ incident: row, run })
      } catch (error) {
        if (!isAbort(error)) setActionError(errorMessage(error))
        setDetail({ incident: row, run: null })
      }
      return
    }
    if (!row.incident_id) {
      setDetail({ incident: row, run: null })
      return
    }
    const path = row.customer.hospital_id
      ? `/admin/operations/hospitals/${row.customer.hospital_id}/incidents/${row.incident_id}`
      : `/admin/operations/incidents/${row.incident_id}`
    try {
      setDetail(await fetchAPI<OperationsIncidentDetail>(path, { signal: controller.signal }))
    } catch (error) {
      if (!isAbort(error)) setActionError(errorMessage(error))
    }
  }, [])

  useEffect(() => {
    const handleVisibility = () => {
      if (document.visibilityState === 'hidden') {
        centerAbort.current?.abort()
        detailAbort.current?.abort()
        return
      }
      void loadCenter(page !== null)
      if (selectedRow) void loadDetail(selectedRow)
    }
    document.addEventListener('visibilitychange', handleVisibility)
    return () => document.removeEventListener('visibilitychange', handleVisibility)
  }, [loadCenter, loadDetail, page, selectedRow])

  useEffect(() => {
    if (selectedRow) void loadDetail(selectedRow)
    else setDetail(null)
    return () => detailAbort.current?.abort()
  }, [loadDetail, selectedRow])

  useEffect(() => {
    if (!detail?.run || !shouldPollRun(detail.run.state)) return
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') void loadDetail(detail.incident)
    }, 3_000)
    return () => window.clearInterval(timer)
  }, [detail, loadDetail])

  const select = useCallback((row: OperationsQueueRow) => {
    setActionError('')
    setPermissionDenied(false)
    patchQuery({ queue: paramQueue(row.queue), detail: row.id })
  }, [patchQuery])

  const mutate = useCallback(async (mutation: OperationMutation) => {
    setBusy(true)
    setActionError('')
    setPermissionDenied(false)
    const body = mutation.kind === 'RETRY_RUN' || mutation.kind === 'POST_ACTION'
      ? { reason: mutation.reason }
      : { expected_version: mutation.version, reason: mutation.reason }
    const headers = mutation.requiresIdempotencyKey
      ? { 'Idempotency-Key': createUserActionKey(mutation.kind, mutation.targetId, crypto.randomUUID()) }
      : undefined
    try {
      await fetchAPI(mutation.path, { method: 'POST', headers, body: JSON.stringify(body) })
      if (selectedRow) await loadDetail(selectedRow)
      await loadCenter(true)
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        const conflict = interpretOperationsConflict(error.detail)
        setActionError(conflict.message)
        if (selectedRow) await loadDetail(selectedRow)
        await loadCenter(true)
        window.requestAnimationFrame(() => {
          const current = document.getElementById('ops-current-action')
          const queue = document.getElementById('ops-queue-heading')
          ;(current ?? queue)?.focus()
        })
      } else if (error instanceof ApiError && error.status === 403) {
        setActionError('이 작업은 권한 있는 담당자만 처리할 수 있습니다. 담당자에게 요청하거나 개발팀 문의 정보를 복사하세요.')
        setPermissionDenied(true)
      } else {
        setActionError(safeOperatorError(
          'operations',
          '같은 처리 버튼을 다시 누르고, 계속 실패하면 개발팀 문의용 정보를 복사하세요.',
        ))
      }
    } finally {
      setBusy(false)
    }
  }, [loadCenter, loadDetail, selectedRow])

  const visibleItems = useMemo(() => {
    const needle = query.q.toLocaleLowerCase('ko-KR')
    if (!needle) return page?.items ?? []
    return (page?.items ?? []).filter((item) => [item.customer.name, item.next_action, item.impact, item.owner?.name ?? ''].some((value) => value.toLocaleLowerCase('ko-KR').includes(needle)))
  }, [page, query.q])

  return { query, overview, page, detail, selectedRow, visibleItems, checkedAt, loading, loadError, actionError, permissionDenied, busy, canRaiseLimit, patchQuery, select, mutate, reload: loadCenter, close: () => patchQuery({ detail: null }) }
}
