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
import { domainSearchText } from '@/lib/hospital-domain-status'
import {
  describeContentState,
  describeDomainState,
  describePublicService,
  stateTone,
  type StateDescription,
  type StateKind,
  type StateTone,
} from '@/lib/hospital-states'
import { ADMIN_COPY } from '@/lib/admin-copy'
import {
  hospitalMatchesStatus,
  hospitalStatusCounts,
  type HospitalStatusFilter,
} from '@/lib/hospital-list-filter'
import { HospitalListRow, PLAN_LABELS } from '@/types'
import { SkeletonTable } from '@/app/components/Skeleton'

// backend GET /admin/hospitals — skip/limit 파라미터 (기본 50, 최대 200)
const PAGE_SIZE = 50

export default function HospitalsPage() {
  const [hospitals, setHospitals] = useState<HospitalListRow[]>([])
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
      const data = await fetchAPI<HospitalListRow[]>(`/admin/hospitals?skip=${skip}&limit=${PAGE_SIZE}`)
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
              MotionLabs가 관리하는 병원 운영 화면입니다. 병원을 선택하면 운영 요약을 볼 수 있습니다.
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
          <div className={attention.unreviewed_total > 0 || attention.withheld_total > 0 ? '' : 'hidden'}>
          <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
            {/* 확인할 것이 없고 보류만 남았으면 "확인 필요 0건"이 제목일 이유가 없다 —
                그때는 실제 할 일인 공개 보류가 제목이 된다(H-01). */}
            <h2 id="attention-heading" className="text-sm font-semibold text-slate-900">
              {attention.unreviewed_total > 0
                ? `공개 후 확인 필요 ${attention.unreviewed_total}건`
                : `공개 보류 ${attention.withheld_total}건`}
            </h2>
            {attention.overdue_total > 0 && (
              <span className="text-xs font-medium text-red-700">
                그중 {attention.overdue_total}건은 {attention.overdue_hours}시간 넘음
              </span>
            )}
            {/* 공개 보류는 확인이 아니라 사유 해소가 할 일이다 — 숫자를 섞지 않는다(H-01). */}
            {attention.unreviewed_total > 0 && attention.withheld_total > 0 && (
              <span className="inline-flex rounded-full bg-amber-50 px-2 py-0.5 text-[11px] font-semibold text-amber-800">
                공개 보류 {attention.withheld_total}건
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
                      {row.withheld_count > 0 && (
                        <span className="ml-1.5 inline-flex rounded-full bg-amber-50 px-2 py-0.5 text-[11px] font-semibold text-amber-800">
                          공개 보류 {row.withheld_count}건
                        </span>
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
                  ...attention.reports.missing.map((row) => ({ row, label: '보고서 없음' })),
                  ...attention.reports.undelivered.map((row) => ({ row, label: '원장에게 전달하지 않음' })),
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
            계약이 체결된 병원을 등록하면 초기 진단은 백그라운드에서 진행되고, 병원 공개 페이지 준비와 콘텐츠 설정은 기다리지 않고 계속할 수 있습니다.
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
              placeholder="병원명, 공개 주소, 도메인 검색"
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
                  <th className="text-left px-6 py-3 text-slate-600 font-medium">공개 서비스</th>
                  <th className="text-left px-6 py-3 text-slate-600 font-medium">콘텐츠 준비</th>
                  <th className="text-left px-6 py-3 text-slate-600 font-medium">자기 도메인</th>
                  <th className="text-left px-6 py-3 text-slate-600 font-medium">{ADMIN_COPY.plan}</th>
                  <th className="px-4 py-3"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {filtered.length === 0 && (
                  <tr>
                    <td colSpan={6} className="text-center py-12 text-slate-400">
                      {query ? '검색 조건에 맞는 병원이 없습니다.' : '선택한 상태의 병원이 없습니다.'}
                    </td>
                  </tr>
                )}
                {filtered.map((h) => {
                  // 상태 판정은 서버가 한다 — 목록이 따로 계산하면 헤더·현황과 다른 답을 낸다.
                  const publicService = describePublicService(h.public_service_state)
                  const contentReady = describeContentState(h.content_state)
                  const domain = describeDomainState(h.domain_state)
                  const domainHref = h.site_built
                    ? `/hospitals/${h.id}/info#domain-setup`
                    : `/hospitals/${h.id}/info`
                  return (
                    <tr key={h.id} className="hover:bg-slate-50/80 transition-colors">
                      <td className="px-6 py-4" data-primary="true">
                        <Link
                          href={`/hospitals/${h.id}`}
                          className="block group"
                        >
                          <div className="font-medium text-slate-900 group-hover:text-blue-700">
                            {h.name}
                            {(reviewCounts.get(h.id) ?? 0) > 0 && (
                              <span className="ml-2 inline-flex rounded-full bg-red-50 px-2 py-0.5 text-[11px] font-semibold text-red-700">
                                {ADMIN_COPY.postPublishReview} 필요 {reviewCounts.get(h.id)}건
                              </span>
                            )}
                            {/* 예외는 사람이 손대야 풀린다 — 목록에서 바로 보이지 않으면
                                병원을 열어 보기 전에는 알 수 없다. */}
                            {h.open_exception_count > 0 && (
                              <span className="ml-2 inline-flex rounded-full bg-amber-50 px-2 py-0.5 text-[11px] font-semibold text-amber-800">
                                예외 {h.open_exception_count}건
                              </span>
                            )}
                          </div>
                          <div className="mt-0.5 text-[11px] text-slate-400">
                            {ADMIN_COPY.aeOwner} {h.ae_owner?.name ?? '미지정'}
                          </div>
                        </Link>
                        {/* 사진 승인은 사람만 풀 수 있고, 여기서 보이지 않으면 병원을 열기
                            전에는 알 수 없다 — 승인 화면으로 바로 간다(O-2). */}
                        {(h.visual_approval_missing?.length ?? 0) > 0 && (
                          <Link
                            href={`/hospitals/${h.id}/info`}
                            className="mt-1 inline-flex rounded-full bg-amber-50 px-2 py-0.5 text-[11px] font-semibold text-amber-800 hover:bg-amber-100"
                          >
                            사진 승인 대기 {h.visual_approval_missing?.length}건
                          </Link>
                        )}
                      </td>
                      <td className="px-6 py-4" data-label="공개 서비스">
                        <StateCell kind={h.public_service_state.kind} description={publicService} />
                      </td>
                      <td className="px-6 py-4" data-label="콘텐츠 준비">
                        <StateCell kind={h.content_state.kind} description={contentReady} />
                      </td>
                      <td className="px-6 py-4" data-label="자기 도메인">
                        {/* 자기 도메인을 쓰지 않는 병원은 이 칸이 비어 있는 게 사실이다 —
                            기본 주소로 공개 중인 병원을 '미설정'으로 부르지 않는다.
                            링크는 사람이 결정할 문제(`problem`)에만 붙인다 — 연결됨·확인
                            중은 손댈 것이 없는데 눌러 보게 만든다. */}
                        {!domain ? (
                          <span className="text-xs text-slate-400">—</span>
                        ) : h.domain_state.kind === 'problem' ? (
                          <Link href={domainHref} className="group inline-flex max-w-[240px]">
                            <StateCell kind={h.domain_state.kind} description={domain} />
                          </Link>
                        ) : (
                          <StateCell kind={h.domain_state.kind} description={domain} />
                        )}
                      </td>
                      <td className="px-6 py-4 text-slate-600" data-label={ADMIN_COPY.plan}>
                        {h.plan ? PLAN_LABELS[h.plan] ?? h.plan : '-'}
                      </td>
                      <td className="px-4 py-4 text-right" data-label="">
                        <Link
                          href={`/hospitals/${h.id}`}
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

function stateToneClass(tone: StateTone): string {
  switch (tone) {
    case 'good':
      return 'bg-emerald-50 text-emerald-700 border border-emerald-200'
    case 'warn':
      return 'bg-amber-50 text-amber-800 border border-amber-200'
    case 'paused':
      return 'bg-slate-100 text-slate-600 border border-slate-200'
    case 'neutral':
      return 'bg-sky-50 text-sky-700 border border-sky-200'
  }
}

/** 상태 하나 — 라벨과 남은 조건. 남은 조건은 사람 몫과 시스템 몫을 나눠 말한다. */
function StateCell({ kind, description }: { kind: StateKind; description: StateDescription }) {
  return (
    <span className="inline-flex max-w-[260px] flex-col">
      <span className={`inline-flex w-fit rounded-full px-2.5 py-0.5 text-xs font-medium ${stateToneClass(stateTone(kind))}`}>
        {description.label}
      </span>
      {description.detail && (
        <span className="mt-1 text-[11px] leading-relaxed text-slate-500">{description.detail}</span>
      )}
    </span>
  )
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

