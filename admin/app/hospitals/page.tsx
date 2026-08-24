'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { fetchAPI } from '@/lib/api'
import { OperatorIssuePanel } from '@/app/_components/OperatorIssuePanel'
import { isExpectedOperatorRequestFailure, safeOperatorError } from '@/lib/operations-journey'
import {
  ATTENTION_VISIBLE_ROWS,
  type AttentionQueue,
  formatWaiting,
  hasAttentionWork,
  hasReportGaps,
  hiddenHospitalCount,
  reportGapSummary,
} from '@/lib/attention-queue'
import { domainSearchText, readHospitalDomainStatus } from '@/lib/hospital-domain-status'
import {
  hospitalMatchesStatus,
  hospitalStatusCounts,
  type HospitalStatusFilter,
} from '@/lib/hospital-list-filter'
import { Hospital, STATUS_LABELS, PLAN_LABELS } from '@/types'
import { SkeletonTable } from '@/app/components/Skeleton'

// backend GET /admin/hospitals — skip/limit 파라미터 (기본 50, 최대 200)
const PAGE_SIZE = 50

export default function HospitalsPage() {
  const [hospitals, setHospitals] = useState<Hospital[]>([])
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [hasMore, setHasMore] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [statusFilter, setStatusFilter] = useState<HospitalStatusFilter>('all')

  const loadPage = useCallback(async (skip: number) => {
    if (skip === 0) setLoading(true)
    else setLoadingMore(true)
    setError(null)
    try {
      const data = await fetchAPI<Hospital[]>(`/admin/hospitals?skip=${skip}&limit=${PAGE_SIZE}`)
      const page = Array.isArray(data) ? data : []
      setHospitals((prev) => (skip === 0 ? page : [...prev, ...page]))
      setHasMore(page.length === PAGE_SIZE)
    } catch (e: unknown) {
      if (!isExpectedOperatorRequestFailure(e)) throw e
      setError(safeOperatorError('onboarding', '병원 목록 다시 불러오기를 누르세요.'))
    } finally {
      setLoading(false)
      setLoadingMore(false)
    }
  }, [])

  useEffect(() => {
    void loadPage(0)
  }, [loadPage])

  // 확인 대기 큐는 부가 정보다 — 실패해도 병원 목록을 막지 않는다.
  const [attention, setAttention] = useState<AttentionQueue | null>(null)
  useEffect(() => {
    fetchAPI<AttentionQueue>('/admin/operations/attention')
      .then(setAttention)
      .catch(() => setAttention(null))
  }, [])

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    return hospitals.filter(
      (hospital) =>
        hospitalMatchesStatus(hospital, statusFilter)
        && (!q || domainSearchText(hospital).includes(q)),
    )
  }, [hospitals, query, statusFilter])

  const stats = useMemo(() => {
    return hospitalStatusCounts(hospitals)
  }, [hospitals])

  const reviewCounts = useMemo(
    () => new Map((attention?.hospitals ?? []).map((row) => [row.hospital_id, row.unreviewed_count])),
    [attention],
  )

  return (
    <div className="p-4 sm:p-6 lg:p-8">
      {/* Page header */}
      <div className="mb-6">
        <div className="flex flex-col gap-4 mb-2 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <h1 className="text-2xl font-bold text-slate-900">병원 목록</h1>
            <p className="text-sm text-slate-500 mt-1">
              MotionLabs가 운영 중인 병원 워크스페이스 — 클릭하여 대시보드로 이동합니다.
            </p>
          </div>
          <Link
            href="/hospitals/new"
            className="inline-flex items-center gap-1.5 px-4 py-2 bg-blue-600 text-white text-sm font-medium rounded-lg hover:bg-blue-700 transition-colors shadow-sm"
          >
            <span aria-hidden>＋</span>
            신규 병원 온보딩
          </Link>
        </div>

        {/* Quick stats */}
        {!loading && !error && hospitals.length > 0 && (
          <div className="flex items-center gap-2 text-xs text-slate-500 mt-4" aria-label="병원 상태 필터">
            <StatPill label="전체" value={stats.total} selected={statusFilter === 'all'} onClick={() => setStatusFilter('all')} />
            <StatPill label="운영 중" value={stats.active} tone="good" selected={statusFilter === 'active'} onClick={() => setStatusFilter('active')} />
            <StatPill label="온보딩" value={stats.onboarding} tone="warn" selected={statusFilter === 'onboarding'} onClick={() => setStatusFilter('onboarding')} />
          </div>
        )}
      </div>

      {hasAttentionWork(attention) && attention && (
        <section
          aria-labelledby="attention-heading"
          className="mb-6 rounded-xl border border-slate-200 bg-white p-4"
        >
          {/* 확인 대기가 0이어도 원장 보고가 밀렸을 수 있다 — 그때는 이 묶음을 감춘다. */}
          <div className={attention.unreviewed_total > 0 ? '' : 'hidden'}>
          <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
            <h2 id="attention-heading" className="text-sm font-semibold text-slate-900">
              공개 후 확인 필요 {attention.unreviewed_total}건
            </h2>
            {attention.overdue_total > 0 && (
              <span className="text-xs font-medium text-red-700">
                그중 {attention.overdue_total}건은 {attention.overdue_hours}시간 넘음
              </span>
            )}
          </div>
          <ul className="mt-3 divide-y divide-slate-100">
            {attention.hospitals.slice(0, ATTENTION_VISIBLE_ROWS).map((row) => {
              const waiting = formatWaiting(row.oldest_published_at)
              return (
                <li key={row.hospital_id}>
                  <Link
                    href={`/hospitals/${row.hospital_id}/content`}
                    className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 py-2 text-sm hover:bg-slate-50"
                  >
                    <span className="font-medium text-slate-900">{row.hospital_name}</span>
                    <span className="text-slate-600">
                      {row.unreviewed_count}건
                      {row.overdue_count > 0 && (
                        <span className="ml-1.5 text-red-700">{row.overdue_count}건 초과</span>
                      )}
                      {waiting && <span className="ml-2 text-slate-400">{waiting}</span>}
                    </span>
                  </Link>
                </li>
              )
            })}
          </ul>
          {hiddenHospitalCount(attention) > 0 && (
            <p className="mt-2 text-xs text-slate-500">
              외 {hiddenHospitalCount(attention)}곳 — 아래 목록에서 확인해 주세요.
            </p>
          )}
          </div>

          {/* 원장 보고는 월 1회짜리 리듬이라 위 큐와 성격이 다르다 — 줄을 나눠 둔다. */}
          {hasReportGaps(attention) && attention.reports && (
            <div className="mt-4 border-t border-slate-100 pt-3">
              <div className="flex flex-wrap items-baseline gap-x-2">
                <h3 className="text-sm font-semibold text-slate-900">
                  {attention.reports.period_month}월 원장 보고
                </h3>
                <span className="text-xs font-medium text-amber-700">
                  {reportGapSummary(attention.reports)}
                </span>
              </div>
              <ul className="mt-2 divide-y divide-slate-100">
                {[
                  ...attention.reports.missing.map((row) => ({ row, label: '리포트 없음' })),
                  ...attention.reports.undelivered.map((row) => ({ row, label: '원장 미전달' })),
                ]
                  .slice(0, ATTENTION_VISIBLE_ROWS)
                  .map(({ row, label }) => (
                    <li key={`${row.hospital_id}-${label}`}>
                      <Link
                        href={`/hospitals/${row.hospital_id}/reports`}
                        className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 py-2 text-sm hover:bg-slate-50"
                      >
                        <span className="font-medium text-slate-900">{row.hospital_name}</span>
                        <span className="text-slate-600">{label}</span>
                      </Link>
                    </li>
                  ))}
              </ul>
            </div>
          )}
        </section>
      )}

      {loading && <SkeletonTable rows={6} />}

      {error && (
        <OperatorIssuePanel message={error} surface="onboarding" onRetry={() => void loadPage(0)} retryLabel="병원 목록 다시 불러오기" />
      )}

      {!loading && !error && hospitals.length === 0 && (
        <div className="bg-white border border-dashed border-slate-300 rounded-xl py-16 px-6 text-center">
          <p className="text-base font-semibold text-slate-700">아직 등록된 병원이 없습니다.</p>
          <p className="text-sm text-slate-500 mt-2 max-w-md mx-auto">
            계약이 체결된 병원을 등록하면 초기 진단 리포트 → 병원 정보 허브 준비 → 콘텐츠 자동 생성 순서로 진행됩니다.
          </p>
          <Link
            href="/hospitals/new"
            className="inline-flex items-center gap-1.5 mt-5 px-4 py-2 bg-blue-600 text-white text-sm font-medium rounded-lg hover:bg-blue-700 transition-colors"
          >
            첫 병원 온보딩 시작
          </Link>
        </div>
      )}

      {!loading && !error && hospitals.length > 0 && (
        <>
          {/* Search */}
          <div className="mb-3 flex flex-col gap-2 sm:flex-row sm:items-center sm:gap-3">
            <input
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="병원명, slug, 도메인 검색"
              className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm bg-white focus:outline-none focus:ring-2 focus:ring-blue-500 sm:w-72"
            />
            {(query || statusFilter !== 'all') && (
              <span className="text-xs text-slate-500">
                {filtered.length}개 표시
              </span>
            )}
          </div>

          <div className="rounded-xl border border-slate-200 bg-white shadow-sm">
            <div className="admin-responsive-table-wrap overflow-x-auto">
            <table className="admin-responsive-table min-w-[980px] w-full text-sm">
              <thead className="bg-slate-50 border-b border-slate-200">
                <tr>
                  <th className="text-left px-6 py-3 text-slate-600 font-medium">병원</th>
                  <th className="text-left px-6 py-3 text-slate-600 font-medium">상태</th>
                  <th className="text-left px-6 py-3 text-slate-600 font-medium">도메인</th>
                  <th className="text-left px-6 py-3 text-slate-600 font-medium">월간 운영량</th>
                  <th className="text-center px-4 py-3 text-slate-600 font-medium">병원 기본 정보</th>
                  <th className="text-center px-4 py-3 text-slate-600 font-medium">병원 정보 허브</th>
                  <th className="text-center px-4 py-3 text-slate-600 font-medium">스케줄</th>
                  <th className="px-4 py-3"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {filtered.length === 0 && (
                  <tr>
                    <td colSpan={8} className="text-center py-12 text-slate-400">
                      {query ? '검색 조건에 맞는 병원이 없습니다.' : '선택한 상태의 병원이 없습니다.'}
                    </td>
                  </tr>
                )}
                {filtered.map((h) => {
                  const status =
                    STATUS_LABELS[h.status] ?? { label: h.status, color: 'bg-slate-100 text-slate-700' }
                  const domainStatus = readHospitalDomainStatus(h)
                  const domainHref = h.site_built
                    ? `/hospitals/${h.id}/profile#domain-setup`
                    : `/hospitals/${h.id}/profile`
                  return (
                    <tr key={h.id} className="hover:bg-slate-50/80 transition-colors">
                      <td className="px-6 py-4" data-primary="true">
                        <Link
                          href={`/hospitals/${h.id}/dashboard`}
                          className="block group"
                        >
                          <div className="font-medium text-slate-900 group-hover:text-blue-700">
                            {h.name}
                            {(reviewCounts.get(h.id) ?? 0) > 0 && (
                              <span className="ml-2 inline-flex rounded-full bg-red-50 px-2 py-0.5 text-[11px] font-semibold text-red-700">
                                공개 후 확인 필요 {reviewCounts.get(h.id)}건
                              </span>
                            )}
                          </div>
                          <div className="text-[11px] text-slate-400 font-mono mt-0.5">
                            {h.slug}
                          </div>
                        </Link>
                      </td>
                      <td className="px-6 py-4" data-label="상태">
                        <span
                          className={`inline-flex px-2.5 py-0.5 rounded-full text-xs font-medium ${status.color}`}
                        >
                          {status.label}
                        </span>
                      </td>
                      <td className="px-6 py-4" data-label="도메인">
                        <Link
                          href={domainHref}
                          className="group inline-flex max-w-[240px] flex-col"
                        >
                          <span
                            className={`inline-flex w-fit rounded-full px-2.5 py-0.5 text-xs font-medium ${domainToneClass(
                              domainStatus.tone,
                            )}`}
                          >
                            {domainStatus.label}
                          </span>
                          <span className="mt-1 truncate text-xs text-slate-500 group-hover:text-blue-700">
                            {domainStatus.detail}
                          </span>
                        </Link>
                      </td>
                      <td className="px-6 py-4 text-slate-600" data-label="월간 운영량">
                        {h.plan ? PLAN_LABELS[h.plan] ?? h.plan : '-'}
                      </td>
                      <td className="px-4 py-4 text-center" data-label="병원 기본 정보">
                        {/* 프로파일 필드가 다 찼어도 공개 표면 시각 승인이 남아 있으면
                            이 단계는 끝난 게 아니다. ✓만 보여 주면 목록과 상세가
                            정면으로 어긋난다(O-2). */}
                        {pendingVisualCount(h) > 0 ? (
                          <Link
                            href={`/hospitals/${h.id}/onboarding`}
                            className="inline-flex items-center whitespace-nowrap rounded-full bg-amber-50 px-2 py-1 text-[11px] font-semibold text-amber-800 hover:bg-amber-100"
                            title={`승인 필요: ${(h.visual_approval_missing ?? []).join(', ')}`}
                          >
                            승인 대기 {pendingVisualCount(h)}건
                          </Link>
                        ) : (
                          <CheckCell done={h.profile_complete} />
                        )}
                      </td>
                      <td className="px-4 py-4 text-center" data-label="정보 허브">
                        <CheckCell done={h.site_live} />
                      </td>
                      <td className="px-4 py-4 text-center" data-label="스케줄">
                        <CheckCell done={h.schedule_set} />
                      </td>
                      <td className="px-4 py-4 text-right" data-label="">
                        <Link
                          href={`/hospitals/${h.id}/dashboard`}
                          className="text-xs font-medium text-blue-600 hover:text-blue-800 hover:underline"
                        >
                          열기 →
                        </Link>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
            </div>
            {hasMore && (
              <div className="border-t border-slate-100 px-6 py-3 text-center">
                <button
                  type="button"
                  onClick={() => loadPage(hospitals.length)}
                  disabled={loadingMore}
                  className="rounded-lg border border-slate-200 bg-white px-4 py-2 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                >
                  {loadingMore ? '불러오는 중...' : '더 보기'}
                </button>
                {query && (
                  <p className="mt-1.5 text-[11px] text-slate-400">
                    검색은 불러온 {hospitals.length}개 병원에서만 동작합니다. 전체에서 찾으려면 더 보기로 목록을 불러오세요.
                  </p>
                )}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  )
}

function domainToneClass(tone: 'live' | 'waiting' | 'dns_verified' | 'issuing' | 'failed' | 'default' | 'empty'): string {
  switch (tone) {
    case 'live':
      return 'bg-emerald-50 text-emerald-700 border border-emerald-200'
    case 'dns_verified':
      return 'bg-emerald-50 text-emerald-600 border border-emerald-200'
    case 'issuing':
      return 'bg-blue-100 text-blue-700 border border-blue-200'
    case 'failed':
      return 'bg-amber-100 text-amber-700 border border-amber-200'
    case 'waiting':
      return 'bg-amber-50 text-amber-700 border border-amber-200'
    case 'default':
      return 'bg-sky-50 text-sky-700 border border-sky-200'
    case 'empty':
      return 'bg-slate-50 text-slate-600 border border-slate-200'
  }
}

function StatPill({
  label,
  value,
  tone,
  selected,
  onClick,
}: {
  label: string
  value: number
  tone?: 'good' | 'warn'
  selected: boolean
  onClick: () => void
}) {
  const dot =
    tone === 'good' ? 'bg-emerald-500' : tone === 'warn' ? 'bg-amber-500' : 'bg-slate-400'
  return (
    <button
      type="button"
      aria-pressed={selected}
      onClick={onClick}
      className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 transition-colors ${
        selected
          ? 'border-blue-300 bg-blue-50 text-blue-800'
          : 'border-slate-200 bg-white hover:border-slate-300 hover:bg-slate-50'
      }`}
    >
      <span className={`w-1.5 h-1.5 rounded-full ${dot}`} aria-hidden />
      <span className="text-slate-500">{label}</span>
      <span className="font-semibold text-slate-700">{value}</span>
    </button>
  )
}

/** 승인이 남은 시각 항목 수. 값을 안 내려주는 구버전 응답은 0으로 본다. */
function pendingVisualCount(hospital: { visual_approval_missing?: string[] }): number {
  return hospital.visual_approval_missing?.length ?? 0
}

function CheckCell({ done }: { done: boolean | undefined }) {
  if (done) {
    return (
      <span
        className="inline-flex items-center justify-center w-5 h-5 rounded-full bg-emerald-100 text-emerald-700 text-xs font-bold"
        title="완료"
        aria-label="완료"
      >
        ✓
      </span>
    )
  }
  return (
    <span
      className="inline-block w-5 h-5 rounded-full border border-dashed border-slate-300"
      title="미완료"
      aria-label="미완료"
    />
  )
}
