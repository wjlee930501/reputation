'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'

import { ApiError, fetchAPI } from '@/lib/api'
import { fetchCurrentAccount } from '@/lib/current-account'
import { safeOperatorError } from '@/lib/operations-journey'
import {
  canonicalizeOperationsQuery,
  getOrCreateOperationsMutationKey,
  interpretOperationsConflict,
  mutationRequestBody,
  readOperationsQuery,
  resolveOperationsDetail,
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

function incidentDetailPath(hospitalId: string | null, incidentId: string): string {
  return hospitalId
    ? `/admin/operations/hospitals/${hospitalId}/incidents/${incidentId}`
    : `/admin/operations/incidents/${incidentId}`
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
  const mutationKeys = useRef(new Map<string, string>())

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
    if (query.hospitalId) filters.set('hospital_id', query.hospitalId)
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
  }, [query.hospitalId, query.owner, query.page, query.queue, query.recovery, query.severity, query.sla, query.status])

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

  const resolution = useMemo(
    () => resolveOperationsDetail(
      query.detail,
      [...(page?.items ?? []), ...(overview?.items ?? [])],
      query.hospitalId,
    ),
    [overview, page, query.detail, query.hospitalId],
  )
  const selectedRow = resolution.kind === 'row' ? resolution.row : null

  const loadDetail = useCallback(async (row: OperationsQueueRow) => {
    detailAbort.current?.abort()
    const controller = new AbortController()
    detailAbort.current = controller
    if (row.operation_run_id && row.customer.hospital_id) {
      // 작업 기록만 읽으면 상세는 담당 후보(`assignable_accounts`)를 영영 받지 못해
      // 담당 지정 폼을 그릴 수 없다. 두 조회를 함께 낸다 — 하나가 실패해도 나머지는 쓴다.
      const [run, incident] = await Promise.all([
        fetchAPI<OperationsRunSummary>(
          `/admin/operations/hospitals/${row.customer.hospital_id}/runs/${row.operation_run_id}`,
          { signal: controller.signal },
        ).catch((error: unknown) => {
          if (!isAbort(error)) setActionError(errorMessage(error))
          return null
        }),
        row.incident_id
          ? fetchAPI<OperationsIncidentDetail>(
              incidentDetailPath(row.customer.hospital_id, row.incident_id),
              { signal: controller.signal },
            ).catch(() => null)
          : Promise.resolve(null),
      ])
      if (controller.signal.aborted) return
      setDetail({
        incident: row,
        run,
        // 조회에 실패했으면 빈 배열로 확정한다 — undefined로 두면 화면이 영원히
        // "담당자 정보를 불러오는 중"에 머문다.
        assignable_accounts: incident?.assignable_accounts ?? [],
      })
      return
    }
    if (!row.incident_id) {
      setDetail({ incident: row, run: null, assignable_accounts: [] })
      return
    }
    try {
      setDetail(await fetchAPI<OperationsIncidentDetail>(
        incidentDetailPath(row.customer.hospital_id, row.incident_id),
        { signal: controller.signal },
      ))
    } catch (error) {
      if (!isAbort(error)) setActionError(errorMessage(error))
    }
  }, [])

  const loadIncidentById = useCallback(async (hospitalId: string, incidentId: string) => {
    detailAbort.current?.abort()
    const controller = new AbortController()
    detailAbort.current = controller
    try {
      setDetail(await fetchAPI<OperationsIncidentDetail>(
        incidentDetailPath(hospitalId, incidentId),
        { signal: controller.signal },
      ))
    } catch (error) {
      if (!isAbort(error)) setActionError(errorMessage(error))
      setDetail(null)
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
    // 링크가 가리키는 인시던트가 현재 쪽의 행과 맞지 않으면 그 건을 직접 읽는다 —
    // 묶여서 접혔거나 다음 쪽에 있다고 상세가 열리지 않으면 안 된다.
    if (resolution.kind === 'row') void loadDetail(resolution.row)
    else if (resolution.kind === 'fetch') {
      void loadIncidentById(resolution.hospitalId, resolution.incidentId)
    } else setDetail(null)
    return () => detailAbort.current?.abort()
  }, [loadDetail, loadIncidentById, resolution])

  useEffect(() => {
    // shouldPollRun(state) is false for every terminal run state (SUCCEEDED/PARTIAL/
    // FAILED/CANCELLED), so this effect naturally stops re-arming the timer once a
    // run finishes — no separate "stop" branch needed.
    if (!detail?.run || !shouldPollRun(detail.run.state)) return
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') void loadDetail(detail.incident)
    }, 5_000)
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
    const assigning = mutation.kind === 'ASSIGN_INCIDENT'
    const body = mutationRequestBody(mutation.kind, {
      reason: mutation.reason,
      expectedVersion: mutation.version,
      ownerId: assigning ? mutation.ownerId ?? null : null,
      slaDueAt: assigning ? mutation.slaDueAt ?? null : null,
    })
    const attempt = mutation.requiresIdempotencyKey
      ? getOrCreateOperationsMutationKey(mutationKeys.current, mutation, crypto.randomUUID())
      : null
    const headers = attempt ? { 'Idempotency-Key': attempt.key } : undefined
    try {
      await fetchAPI(mutation.path, { method: 'POST', headers, body: JSON.stringify(body) })
      // POST 응답을 받았으면 이 요청의 결과는 확정됐다. 뒤따르는 조회가 실패해도 같은
      // 변경을 다시 요청하지 않도록 여기서 요청 키의 수명을 끝낸다.
      if (attempt) mutationKeys.current.delete(attempt.fingerprint)
      if (selectedRow) await loadDetail(selectedRow)
      await loadCenter(true)
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        if (attempt) mutationKeys.current.delete(attempt.fingerprint)
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
        if (attempt) mutationKeys.current.delete(attempt.fingerprint)
        setActionError('이 작업은 권한 있는 담당자만 처리할 수 있습니다. 담당자에게 요청하거나 개발팀 문의 정보를 복사하세요.')
        setPermissionDenied(true)
        // 서버가 거절한 행동은 화면에도 남아 있으면 안 된다 — 최신 인가로 다시 그린다.
        if (selectedRow) await loadDetail(selectedRow)
        await loadCenter(true)
      } else {
        setActionError(safeOperatorError(
          'operations',
          '같은 처리 버튼을 다시 누르면 처음 요청과 같은 번호로 결과를 확인합니다. 계속 실패하면 개발팀 문의용 정보를 복사하세요.',
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
