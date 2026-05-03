'use client'

import Link from 'next/link'
import { useEffect, useState } from 'react'
import { useParams } from 'next/navigation'
import { fetchAPI } from '@/lib/api'
import {
  EXPOSURE_ACTION_STATUS_LABELS,
  EXPOSURE_ACTION_TYPE_LABELS,
  type AIQueryTarget,
  type ExposureAction,
  type MeasurementRun,
} from '@/types'
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from 'recharts'

interface TrendPoint {
  week_start: string
  sov_pct: number
  mention_count: number
  total_count: number
}

interface QueryRow {
  query_id: string
  query_text: string
  mention_rate: number
  mention_count: number
  total_count: number
  last_measured_at: string | null
}

interface ReadinessCheck {
  key: string
  label: string
  passed: boolean
  weight: number
  next_action: string
}

interface Readiness {
  score: number
  status: string
  published_content_count: number
  sov_record_count: number
  report_count: number
  checks: ReadinessCheck[]
}

function formatDateTime(value: string | null) {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '-'

  return date.toLocaleString('ko-KR', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function formatMeasurementMethod(method: string) {
  const labels: Record<string, string> = {
    OPENAI_RESPONSE: 'OpenAI 응답 측정',
    OPENAI_SEARCH: 'OpenAI Search',
    CHATGPT_SEARCH: 'ChatGPT Search',
  }

  return labels[method] ?? method
}

function formatRunStatus(status: string) {
  const labels: Record<string, string> = {
    PENDING: '대기',
    RUNNING: '실행 중',
    COMPLETED: '완료',
    FAILED: '실패',
    PARTIAL: '일부 완료',
  }

  return labels[status] ?? status
}

function getExposureActionTypeLabel(actionType: string) {
  return EXPOSURE_ACTION_TYPE_LABELS[actionType] ?? {
    label: actionType,
    color: 'bg-slate-50 text-slate-700 border-slate-200',
  }
}

function getExposureActionStatusLabel(status: string) {
  return EXPOSURE_ACTION_STATUS_LABELS[status] ?? {
    label: status,
    color: 'bg-slate-50 text-slate-700 border-slate-200',
  }
}

export default function DashboardPage() {
  const { id } = useParams<{ id: string }>()
  const [trendData, setTrendData] = useState<TrendPoint[]>([])
  const [queries, setQueries] = useState<QueryRow[]>([])
  const [readiness, setReadiness] = useState<Readiness | null>(null)
  const [measurementRuns, setMeasurementRuns] = useState<MeasurementRun[]>([])
  const [exposureActions, setExposureActions] = useState<ExposureAction[]>([])
  const [queryTargets, setQueryTargets] = useState<AIQueryTarget[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setLoading(true)
    Promise.all([
      fetchAPI(`/admin/hospitals/${id}/sov/trend`).catch(() => [] as TrendPoint[]),
      fetchAPI(`/admin/hospitals/${id}/sov/queries`).catch(() => [] as QueryRow[]),
      fetchAPI(`/admin/hospitals/${id}/readiness`).catch(() => null as Readiness | null),
      fetchAPI(`/admin/hospitals/${id}/sov/measurement-runs`).catch(() => [] as MeasurementRun[]),
      fetchAPI(`/admin/hospitals/${id}/exposure-actions?limit=5`).catch(() => [] as ExposureAction[]),
      fetchAPI(`/admin/hospitals/${id}/query-targets`).catch(() => [] as AIQueryTarget[]),
    ])
      .then((
        [trend, qs, readinessData, runs, actions, targets]: [
          TrendPoint[],
          QueryRow[],
          Readiness | null,
          MeasurementRun[],
          ExposureAction[],
          AIQueryTarget[],
        ],
      ) => {
        setTrendData(Array.isArray(trend) ? trend : [])
        setQueries(Array.isArray(qs) ? qs : [])
        setReadiness(readinessData)
        setMeasurementRuns(Array.isArray(runs) ? runs : [])
        setExposureActions(Array.isArray(actions) ? actions : [])
        setQueryTargets(Array.isArray(targets) ? targets : [])
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false))
  }, [id])

  const lastPoint = trendData.length > 0 ? trendData[trendData.length - 1] : null
  const prevPoint = trendData.length > 1 ? trendData[trendData.length - 2] : null
  const currentSov = lastPoint?.sov_pct ?? null
  const prevSov = prevPoint?.sov_pct ?? null
  const change = currentSov !== null && prevSov !== null ? currentSov - prevSov : null
  const queryCount = queries.length
  const latestMeasurementRuns = measurementRuns.slice(0, 3)
  const topExposureActions = exposureActions.slice(0, 3)

  const activeTargets = queryTargets.filter((target) => target.status === 'ACTIVE')
  const nonArchivedTargets = queryTargets.filter((target) => target.status !== 'ARCHIVED')
  const lastRun = measurementRuns[0] ?? null
  const openActionCount = exposureActions.filter(
    (action) => action.status === 'OPEN' || action.status === 'IN_PROGRESS',
  ).length
  const blockedActionCount = exposureActions.filter((action) => action.status === 'BLOCKED').length

  const hasQueryTargets = activeTargets.length > 0
  const hasMeasurement = measurementRuns.some(
    (run) => run.status === 'COMPLETED' || run.status === 'PARTIAL',
  )
  const hasExposureActions = exposureActions.length > 0
  const hasBrief = (readiness?.published_content_count ?? 0) > 0

  const queryTargetsHref = `/hospitals/${id}/query-targets`
  const contentHref = `/hospitals/${id}/content`
  const reportsHref = `/hospitals/${id}/reports`

  type NextStep = { label: string; href: string; hint: string }
  const nextStep: NextStep = !hasQueryTargets
    ? {
        label: 'Query Target 정의',
        href: queryTargetsHref,
        hint: '환자가 ChatGPT·Gemini에서 던지는 질문을 운영 단위로 만듭니다.',
      }
    : !hasMeasurement
      ? {
          label: '베이스라인 SoV 측정',
          href: queryTargetsHref,
          hint: '쿼리 타깃별로 첫 노출 측정을 실행합니다.',
        }
      : !hasExposureActions
        ? {
            label: '노출 진단·액션 검토',
            href: queryTargetsHref,
            hint: '측정 결과의 부족 원인을 진단하고 개선 액션을 만듭니다.',
          }
        : !hasBrief
          ? {
              label: '쿼리 연결 콘텐츠 brief',
              href: contentHref,
              hint: '확정된 액션을 이번 달 콘텐츠 brief로 이어 붙입니다.',
            }
          : {
              label: '재측정·월간 환류',
              href: reportsHref,
              hint: '발행 후 재측정 결과를 다음 액션으로 환류합니다.',
            }

  const isAnalyticsEmpty = !loading && !error && trendData.length === 0

  return (
    <main className="p-8 space-y-6 bg-slate-50 min-h-full">
      {/* Hero */}
      <section className="rounded-2xl bg-gradient-to-br from-slate-900 via-slate-800 to-blue-900 p-7 text-white shadow-sm">
        <p className="text-[11px] font-semibold uppercase tracking-[0.2em] text-blue-200">
          AI Exposure Operations
        </p>
        <div className="mt-2 flex flex-col gap-5 lg:flex-row lg:items-end lg:justify-between">
          <div className="max-w-2xl">
            <h2 className="text-2xl font-bold">AI 노출 운영 보드</h2>
            <p className="mt-2 text-sm leading-6 text-blue-50/90">
              ChatGPT·Gemini 쿼리 타깃 → SoV 측정 → 노출 진단·액션 → 쿼리 연결 콘텐츠 brief를
              한 화면에서 운영합니다. AI가 이해하기 좋은 정보 구조를 만들어 노출을 개선하는
              내부 콘솔이며, 노출 보장이 아니라 개선·재측정 루프를 관리합니다.
            </p>
          </div>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:min-w-[440px]">
            <HeroStat
              label="쿼리 타깃"
              value={`${activeTargets.length}/${nonArchivedTargets.length}`}
              hint="운영중 / 전체"
            />
            <HeroStat
              label="현재 SoV"
              value={currentSov !== null ? `${currentSov.toFixed(1)}%` : '-'}
              hint={
                change !== null
                  ? `전주 대비 ${change > 0 ? '+' : ''}${change.toFixed(1)}%p`
                  : '베이스라인 측정 전'
              }
              tone={change === null ? 'neutral' : change >= 0 ? 'up' : 'down'}
            />
            <HeroStat
              label="진행중 액션"
              value={`${openActionCount}건`}
              hint={
                blockedActionCount > 0
                  ? `확인필요 ${blockedActionCount}건`
                  : `누적 ${exposureActions.length}건`
              }
            />
            <HeroStat
              label="준비도"
              value={readiness ? String(readiness.score) : '-'}
              hint={readiness ? '/ 100' : '측정 후 산출'}
            />
          </div>
        </div>

        <div className="mt-5 flex flex-wrap items-center gap-3">
          <span className="text-xs uppercase tracking-[0.18em] text-blue-200">다음 단계</span>
          <Link
            href={nextStep.href}
            className="inline-flex items-center gap-1.5 rounded-full bg-white px-4 py-1.5 text-xs font-semibold text-slate-900 shadow-sm transition-colors hover:bg-blue-50"
          >
            {nextStep.label}
            <span aria-hidden>→</span>
          </Link>
          <span className="text-xs text-blue-100/80">{nextStep.hint}</span>
        </div>
      </section>

      {error && (
        <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          오류: {error}
        </div>
      )}

      {loading && (
        <div className="rounded-2xl border border-slate-200 bg-white p-10 text-center text-sm text-slate-500">
          운영 보드 데이터를 불러오는 중입니다.
        </div>
      )}

      {/* Workflow strip */}
      {!loading && (
        <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
          <div className="flex items-end justify-between gap-3">
            <div>
              <h3 className="text-sm font-semibold text-slate-900">운영 흐름</h3>
              <p className="mt-1 text-xs text-slate-500">
                각 단계의 상태가 다음 단계의 입력이 됩니다. 측정 → 진단 → 액션 → 콘텐츠 → 재측정
                순서로 흐름을 유지합니다.
              </p>
            </div>
          </div>
          <ol className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            <WorkflowStep
              index={1}
              title="Query Target"
              caption="환자 질문 정의"
              done={hasQueryTargets}
              summary={
                hasQueryTargets
                  ? `${activeTargets.length}개 운영중`
                  : '운영 중인 쿼리 타깃이 없습니다.'
              }
              href={queryTargetsHref}
              cta={hasQueryTargets ? '관리' : '만들기'}
            />
            <WorkflowStep
              index={2}
              title="SoV 측정"
              caption="ChatGPT·Gemini"
              done={hasMeasurement}
              summary={
                lastRun
                  ? `최근 ${formatRunStatus(lastRun.status)} · ${formatDateTime(
                      lastRun.completed_at ?? lastRun.started_at,
                    )}`
                  : '베이스라인 측정 전'
              }
              href={queryTargetsHref}
              cta={hasMeasurement ? '재측정' : '베이스라인 측정'}
              disabled={!hasQueryTargets}
            />
            <WorkflowStep
              index={3}
              title="노출 진단·액션"
              caption="개선 액션 도출"
              done={hasExposureActions}
              summary={
                hasExposureActions
                  ? `진행중 ${openActionCount}건${
                      blockedActionCount > 0 ? ` · 확인필요 ${blockedActionCount}건` : ''
                    }`
                  : '진단 결과가 아직 없습니다.'
              }
              href={queryTargetsHref}
              cta={hasExposureActions ? '검토' : '진단 시작'}
              disabled={!hasMeasurement}
            />
            <WorkflowStep
              index={4}
              title="쿼리 연결 콘텐츠"
              caption="brief → 발행"
              done={hasBrief}
              summary={
                readiness
                  ? `누적 발행 ${readiness.published_content_count}편`
                  : '아직 발행된 콘텐츠가 없습니다.'
              }
              href={contentHref}
              cta={hasBrief ? '편성' : 'brief 생성'}
              disabled={!hasExposureActions}
            />
          </ol>
        </section>
      )}

      {/* Measurement runs */}
      {!loading && (
        <section className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
          <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
            <div>
              <h3 className="text-base font-semibold text-slate-900">측정 실행 로그</h3>
              <p className="mt-1 text-sm text-slate-500">
                측정 메서드는 실행 단위 값이며, 명시되지 않은 실행을 ChatGPT Search로 단정하지
                않습니다. 성공/실패 집계와 실패율은 안정성 지표이며 SoV 분모와 분리됩니다.
              </p>
            </div>
            <Link
              href={queryTargetsHref}
              className="self-start rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50"
            >
              측정 실행 →
            </Link>
          </div>

          {latestMeasurementRuns.length === 0 ? (
            <EmptyHint
              title={
                hasQueryTargets
                  ? '아직 측정 실행이 없습니다.'
                  : 'Query Target을 먼저 만든 뒤 측정을 시작합니다.'
              }
              hint={
                hasQueryTargets
                  ? '쿼리 타깃 화면에서 베이스라인 측정을 실행하세요. 첫 측정 후 SoV 추이가 누적됩니다.'
                  : '운영 흐름은 쿼리 타깃 정의 → 베이스라인 측정 순서로 진행됩니다.'
              }
              ctaLabel={hasQueryTargets ? '베이스라인 측정 실행' : 'Query Target 만들기'}
              ctaHref={queryTargetsHref}
            />
          ) : (
            <div className="mt-4 divide-y divide-slate-100">
              {latestMeasurementRuns.map((run) => (
                <div
                  key={run.id}
                  className="grid gap-3 py-4 md:grid-cols-[1.2fr_1fr_1fr] md:items-center"
                >
                  <div>
                    <p className="text-sm font-medium text-slate-900">
                      {run.run_label || '측정 실행'} ·{' '}
                      <RunStatusPill status={run.status} />
                    </p>
                    <p className="mt-1 text-xs text-slate-500">
                      AI 노출 측정 방식: {formatMeasurementMethod(run.measurement_method)}
                    </p>
                  </div>
                  <div className="text-sm text-slate-700">
                    <p>
                      성공 {run.success_count}/{run.query_count} · 실패 {run.failure_count}
                    </p>
                    <p className="mt-1 text-xs text-slate-500">
                      실패율 {run.failure_rate.toFixed(1)}% · SoV 분모 제외
                    </p>
                  </div>
                  <div className="text-xs text-slate-500 md:text-right">
                    <p>시작 {formatDateTime(run.started_at)}</p>
                    <p className="mt-1">완료 {formatDateTime(run.completed_at)}</p>
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>
      )}

      {/* Top exposure actions */}
      {!loading && (
        <section className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
          <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
            <div>
              <h3 className="text-base font-semibold text-slate-900">이번 달 AI 노출 개선 TOP 3</h3>
              <p className="mt-1 text-sm text-slate-500">
                쿼리 타깃 SoV 진단에서 우선순위가 높은 개선 액션을 표시합니다. 상세 편집은 쿼리
                타깃 화면에서 진행합니다.
              </p>
            </div>
            <Link
              href={queryTargetsHref}
              className="self-start rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50"
            >
              액션 전체 보기 →
            </Link>
          </div>

          {topExposureActions.length === 0 ? (
            <EmptyHint
              title={
                hasMeasurement
                  ? '진단 결과가 아직 없습니다.'
                  : '측정이 끝나야 진단·액션이 생성됩니다.'
              }
              hint={
                hasMeasurement
                  ? '쿼리 타깃 화면에서 SoV 진단을 실행해 부족 원인과 개선 액션을 만들어 주세요.'
                  : '베이스라인 측정 후 쿼리별 노출 부족 원인이 진단되며, 개선 액션이 자동으로 제안됩니다.'
              }
              ctaLabel={hasMeasurement ? '진단·액션 검토' : '베이스라인 측정으로 이동'}
              ctaHref={queryTargetsHref}
            />
          ) : (
            <div className="mt-4 grid gap-3">
              {topExposureActions.map((action) => {
                const actionType = getExposureActionTypeLabel(action.action_type)
                const actionStatus = getExposureActionStatusLabel(action.status)
                return (
                  <div
                    key={action.id}
                    className="rounded-xl border border-slate-200 bg-slate-50 p-4"
                  >
                    <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <span
                            className={`rounded-full border px-2 py-0.5 text-xs font-medium ${actionType.color}`}
                          >
                            {actionType.label}
                          </span>
                          <span
                            className={`rounded-full border px-2 py-0.5 text-xs font-medium ${actionStatus.color}`}
                          >
                            {actionStatus.label}
                          </span>
                          <span className="rounded-full border border-slate-200 bg-white px-2 py-0.5 text-xs font-medium text-slate-600">
                            {action.due_month ?? '월 미정'}
                          </span>
                        </div>
                        <p className="mt-2 text-sm font-semibold text-slate-900">{action.title}</p>
                        <p className="mt-1 text-sm leading-6 text-slate-600">
                          {action.description}
                        </p>
                      </div>
                      <div className="shrink-0 text-left md:w-56 md:text-right">
                        <p className="text-[11px] font-medium uppercase tracking-wide text-slate-400">
                          연결 타깃
                        </p>
                        <p className="mt-1 text-sm font-semibold text-slate-900">
                          {action.query_target?.name ?? '-'}
                        </p>
                        {action.query_target?.target_intent && (
                          <p className="mt-1 text-xs text-slate-500">
                            {action.query_target.target_intent}
                          </p>
                        )}
                      </div>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </section>
      )}

      {/* Readiness + Trend + Queries */}
      {!loading && (
        <section className="space-y-6">
          {readiness && (
            <div className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
              <div className="flex items-start justify-between gap-6">
                <div>
                  <h3 className="text-base font-semibold text-slate-900">AI 검색 준비도</h3>
                  <p className="mt-1 text-sm text-slate-500">
                    OpenAI Search crawler, Google 로컬 엔티티, 공개 콘텐츠, 측정 데이터를 기준으로
                    계산합니다.
                  </p>
                </div>
                <div className="text-right">
                  <p className="text-3xl font-bold text-slate-900">{readiness.score}</p>
                  <p className="text-xs text-slate-400">/ 100</p>
                </div>
              </div>
              <div className="mt-5 grid gap-3 md:grid-cols-3">
                {readiness.checks.map((check) => (
                  <div
                    key={check.key}
                    className={`rounded-xl border p-3 ${
                      check.passed
                        ? 'border-emerald-200 bg-emerald-50'
                        : 'border-slate-200 bg-slate-50'
                    }`}
                  >
                    <p
                      className={`text-sm font-medium ${
                        check.passed ? 'text-emerald-800' : 'text-slate-800'
                      }`}
                    >
                      {check.passed ? '완료' : '필요'} · {check.label}
                    </p>
                    {!check.passed && (
                      <p className="mt-1 text-xs leading-relaxed text-slate-500">
                        {check.next_action}
                      </p>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {isAnalyticsEmpty ? (
            <div className="rounded-2xl border border-dashed border-slate-200 bg-white p-10 text-center">
              <p className="text-sm font-semibold text-slate-700">
                SoV 추이는 첫 주간 측정이 끝난 뒤부터 누적됩니다.
              </p>
              <p className="mt-2 text-xs text-slate-500">
                위 운영 흐름에서 베이스라인 측정을 먼저 실행해 주세요.
              </p>
            </div>
          ) : (
            <>
              <div className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
                <div className="flex items-center justify-between">
                  <h3 className="text-base font-semibold text-slate-900">SoV 주간 추이</h3>
                  <span className="text-xs text-slate-400">
                    측정 쿼리 {queryCount}개 · 누적 {trendData.length}주
                  </span>
                </div>
                <div className="mt-4">
                  <ResponsiveContainer width="100%" height={280}>
                    <LineChart
                      data={trendData}
                      margin={{ top: 5, right: 20, left: 0, bottom: 5 }}
                    >
                      <XAxis dataKey="week_start" tick={{ fontSize: 12 }} />
                      <YAxis domain={[0, 100]} tick={{ fontSize: 12 }} unit="%" />
                      <Tooltip
                        formatter={(value) =>
                          typeof value === 'number' ? `${value.toFixed(1)}%` : value
                        }
                      />
                      <Legend />
                      <Line
                        dataKey="sov_pct"
                        stroke="#1A4B8C"
                        strokeWidth={2}
                        name="SoV"
                        dot={false}
                        connectNulls
                      />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              </div>

              {queries.length > 0 && (
                <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
                  <div className="border-b border-slate-100 px-6 py-4">
                    <h3 className="text-base font-semibold text-slate-900">쿼리별 멘션율</h3>
                    <p className="mt-1 text-xs text-slate-500">
                      쿼리 변형 단위 멘션율입니다. 운영 액션 우선순위를 정하는 보조 지표로 사용합니다.
                    </p>
                  </div>
                  <table className="w-full text-sm">
                    <thead className="border-b border-slate-200 bg-slate-50">
                      <tr>
                        <th className="px-6 py-3 text-left font-medium text-slate-600">쿼리</th>
                        <th className="px-6 py-3 text-center font-medium text-slate-600">멘션율</th>
                        <th className="px-6 py-3 text-center font-medium text-slate-600">최근 측정</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                      {queries.map((q) => (
                        <tr key={q.query_id} className="transition-colors hover:bg-slate-50">
                          <td className="px-6 py-3 text-slate-700">{q.query_text}</td>
                          <td className="px-6 py-3 text-center">
                            <span
                              className={`font-medium ${
                                q.mention_rate >= 50 ? 'text-emerald-600' : 'text-slate-500'
                              }`}
                            >
                              {q.mention_rate.toFixed(0)}%
                            </span>
                          </td>
                          <td className="px-6 py-3 text-center text-xs text-slate-400">
                            {q.last_measured_at
                              ? new Date(q.last_measured_at).toLocaleDateString('ko-KR')
                              : '-'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          )}
        </section>
      )}
    </main>
  )
}

function HeroStat({
  label,
  value,
  hint,
  tone = 'neutral',
}: {
  label: string
  value: string
  hint?: string
  tone?: 'neutral' | 'up' | 'down'
}) {
  const toneClass =
    tone === 'up'
      ? 'text-emerald-200'
      : tone === 'down'
        ? 'text-rose-200'
        : 'text-blue-100/80'
  return (
    <div className="rounded-xl bg-white/10 px-4 py-3 backdrop-blur">
      <p className="text-[11px] font-medium uppercase tracking-wide text-blue-100/70">{label}</p>
      <p className="mt-1 text-xl font-bold text-white">{value}</p>
      {hint && <p className={`mt-1 text-[11px] ${toneClass}`}>{hint}</p>}
    </div>
  )
}

function WorkflowStep({
  index,
  title,
  caption,
  done,
  summary,
  href,
  cta,
  disabled,
}: {
  index: number
  title: string
  caption: string
  done: boolean
  summary: string
  href: string
  cta: string
  disabled?: boolean
}) {
  return (
    <li
      className={`relative flex flex-col rounded-xl border p-4 transition-colors ${
        done
          ? 'border-emerald-200 bg-emerald-50/40'
          : disabled
            ? 'border-slate-200 bg-slate-50'
            : 'border-blue-200 bg-blue-50/40'
      }`}
    >
      <div className="flex items-center gap-2">
        <span
          className={`inline-flex h-6 w-6 items-center justify-center rounded-full text-[11px] font-semibold ${
            done
              ? 'bg-emerald-500 text-white'
              : disabled
                ? 'bg-slate-200 text-slate-500'
                : 'bg-blue-600 text-white'
          }`}
        >
          {done ? '✓' : index}
        </span>
        <div>
          <p className="text-sm font-semibold text-slate-900">{title}</p>
          <p className="text-[11px] text-slate-500">{caption}</p>
        </div>
      </div>
      <p className="mt-3 flex-1 text-xs leading-5 text-slate-600">{summary}</p>
      <Link
        href={href}
        className={`mt-3 inline-flex items-center justify-between rounded-lg px-3 py-1.5 text-xs font-medium transition-colors ${
          done
            ? 'bg-white text-emerald-700 ring-1 ring-emerald-200 hover:bg-emerald-50'
            : disabled
              ? 'bg-white text-slate-500 ring-1 ring-slate-200 hover:bg-slate-50'
              : 'bg-blue-600 text-white hover:bg-blue-700'
        }`}
      >
        <span>{cta}</span>
        <span aria-hidden>→</span>
      </Link>
    </li>
  )
}

function RunStatusPill({ status }: { status: string }) {
  const tone =
    status === 'COMPLETED'
      ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
      : status === 'RUNNING' || status === 'PENDING'
        ? 'bg-blue-50 text-blue-700 border-blue-200'
        : status === 'PARTIAL'
          ? 'bg-amber-50 text-amber-700 border-amber-200'
          : status === 'FAILED'
            ? 'bg-rose-50 text-rose-700 border-rose-200'
            : 'bg-slate-50 text-slate-700 border-slate-200'

  return (
    <span className={`inline-flex rounded-full border px-2 py-0.5 text-[11px] font-medium ${tone}`}>
      {formatRunStatus(status)}
    </span>
  )
}

function EmptyHint({
  title,
  hint,
  ctaLabel,
  ctaHref,
}: {
  title: string
  hint: string
  ctaLabel: string
  ctaHref: string
}) {
  return (
    <div className="mt-4 rounded-xl border border-dashed border-slate-200 bg-slate-50 px-5 py-6">
      <p className="text-sm font-semibold text-slate-700">{title}</p>
      <p className="mt-1 text-xs leading-5 text-slate-500">{hint}</p>
      <Link
        href={ctaHref}
        className="mt-3 inline-flex items-center gap-1.5 rounded-lg bg-slate-900 px-3 py-1.5 text-xs font-semibold text-white hover:bg-slate-800"
      >
        {ctaLabel}
        <span aria-hidden>→</span>
      </Link>
    </div>
  )
}
