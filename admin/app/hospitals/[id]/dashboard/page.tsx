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
        label: '환자 질문 정의',
        href: queryTargetsHref,
        hint: '환자가 ChatGPT·Gemini 같은 AI 답변 서비스에 묻는 질문을 운영 단위로 정리합니다.',
      }
    : !hasMeasurement
      ? {
          label: '첫 AI 언급률 측정',
          href: queryTargetsHref,
          hint: '환자 질문별로 우리 병원이 AI 답변에 얼마나 등장하는지 처음 측정합니다.',
        }
      : !hasExposureActions
        ? {
            label: 'AI 노출 진단·보완 작업 검토',
            href: queryTargetsHref,
            hint: '측정 결과에서 부족한 부분을 진단하고, AI에 더 잘 노출되도록 보완할 작업을 정리합니다.',
          }
        : !hasBrief
          ? {
              label: '환자 질문에 맞춘 콘텐츠 가이드 작성',
              href: contentHref,
              hint: '확정된 보완 작업을 이번 달 콘텐츠 작성 가이드로 이어 붙입니다.',
            }
          : {
              label: '재측정·월간 회고',
              href: reportsHref,
              hint: '발행 후 다시 측정한 결과를 다음 달 작업으로 이어 갑니다.',
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
              환자 질문 정의 → AI 언급률 측정 → 부족한 부분 진단·보완 작업 → 환자 질문에 맞춘 콘텐츠 가이드 작성을
              한 화면에서 운영합니다. AI가 우리 병원을 정확히 이해하고 추천 후보에 올리도록 정보 구조를 다듬는
              내부 콘솔이며, 노출을 보장하는 게 아니라 개선과 재측정을 반복하는 흐름을 관리합니다.
            </p>
          </div>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:min-w-[440px]">
            <HeroStat
              label="환자 질문"
              value={`${activeTargets.length}/${nonArchivedTargets.length}`}
              hint="운영중 / 전체"
            />
            <HeroStat
              label="현재 AI 언급률"
              value={currentSov !== null ? `${currentSov.toFixed(1)}%` : '-'}
              hint={
                change !== null
                  ? `전주 대비 ${change > 0 ? '+' : ''}${change.toFixed(1)}%p`
                  : '첫 측정 전'
              }
              tone={change === null ? 'neutral' : change >= 0 ? 'up' : 'down'}
            />
            <HeroStat
              label="진행중 보완 작업"
              value={`${openActionCount}건`}
              hint={
                blockedActionCount > 0
                  ? `확인필요 ${blockedActionCount}건`
                  : `누적 ${exposureActions.length}건`
              }
            />
            <HeroStat
              label="AI 노출 준비도"
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
                각 단계의 결과가 다음 단계의 재료가 됩니다. 측정 → 진단 → 보완 작업 → 콘텐츠 → 재측정
                순서로 흐름을 유지합니다.
              </p>
            </div>
          </div>
          <ol className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            <WorkflowStep
              index={1}
              title="환자 질문"
              caption="AI에 노출시킬 질문 정의"
              done={hasQueryTargets}
              summary={
                hasQueryTargets
                  ? `${activeTargets.length}개 운영중`
                  : '운영 중인 환자 질문이 없습니다.'
              }
              href={queryTargetsHref}
              cta={hasQueryTargets ? '관리' : '만들기'}
            />
            <WorkflowStep
              index={2}
              title="AI 언급률 측정"
              caption="ChatGPT·Gemini 답변 확인"
              done={hasMeasurement}
              summary={
                lastRun
                  ? `최근 ${formatRunStatus(lastRun.status)} · ${formatDateTime(
                      lastRun.completed_at ?? lastRun.started_at,
                    )}`
                  : '첫 측정 전'
              }
              href={queryTargetsHref}
              cta={hasMeasurement ? '재측정' : '첫 측정'}
              disabled={!hasQueryTargets}
            />
            <WorkflowStep
              index={3}
              title="AI 노출 진단·보완 작업"
              caption="부족한 부분 보완 정리"
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
              title="환자 질문 연결 콘텐츠"
              caption="콘텐츠 가이드 → 발행"
              done={hasBrief}
              summary={
                readiness
                  ? `누적 발행 ${readiness.published_content_count}편`
                  : '아직 발행된 콘텐츠가 없습니다.'
              }
              href={contentHref}
              cta={hasBrief ? '편성' : '콘텐츠 가이드 만들기'}
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
                측정 방식은 실행 단위로 기록되며, 명시되지 않은 실행을 ChatGPT Search로 단정하지
                않습니다. 성공/실패 집계와 실패율은 측정 안정성 지표로 따로 보고, AI 언급률 계산에는 들어가지 않습니다.
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
                  : '환자 질문을 먼저 만든 뒤 측정을 시작합니다.'
              }
              hint={
                hasQueryTargets
                  ? '환자 질문 화면에서 첫 측정을 실행하세요. 측정이 끝나면 AI 언급률 추이가 누적됩니다.'
                  : '운영 흐름은 환자 질문 정의 → 첫 측정 순서로 진행됩니다.'
              }
              ctaLabel={hasQueryTargets ? '첫 측정 실행' : '환자 질문 만들기'}
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
                      측정 방식: {formatMeasurementMethod(run.measurement_method)}
                    </p>
                  </div>
                  <div className="text-sm text-slate-700">
                    <p>
                      성공 {run.success_count}/{run.query_count} · 실패 {run.failure_count}
                    </p>
                    <p className="mt-1 text-xs text-slate-500">
                      실패율 {run.failure_rate.toFixed(1)}% · AI 언급률 계산에서 제외
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
                환자 질문별 AI 언급률 진단에서 우선순위가 높은 보완 작업을 표시합니다. 상세 편집은 환자 질문 화면에서 진행합니다.
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
                  : '측정이 끝나야 진단·보완 작업이 생성됩니다.'
              }
              hint={
                hasMeasurement
                  ? '환자 질문 화면에서 AI 언급률 진단을 실행해 부족한 부분과 보완 작업을 만들어 주세요.'
                  : '첫 측정 후 환자 질문별로 AI에 부족한 부분이 진단되고, 보완 작업이 자동으로 제안됩니다.'
              }
              ctaLabel={hasMeasurement ? '진단·보완 작업 검토' : '첫 측정으로 이동'}
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
                          연결된 환자 질문
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
                  <h3 className="text-base font-semibold text-slate-900">AI 노출 준비도</h3>
                  <p className="mt-1 text-sm text-slate-500">
                    OpenAI 검색 크롤러 접근, Google 로컬에 등록된 병원 정보, 공개 콘텐츠, 측정 데이터를 기준으로 계산합니다.
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
                AI 언급률 추이는 첫 주간 측정이 끝난 뒤부터 누적됩니다.
              </p>
              <p className="mt-2 text-xs text-slate-500">
                위 운영 흐름에서 첫 측정을 먼저 실행해 주세요.
              </p>
            </div>
          ) : (
            <>
              <div className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
                <div className="flex items-center justify-between">
                  <h3 className="text-base font-semibold text-slate-900">AI 언급률 주간 추이</h3>
                  <span className="text-xs text-slate-400">
                    측정한 환자 질문 {queryCount}개 · 누적 {trendData.length}주
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
                        name="AI 언급률"
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
                    <h3 className="text-base font-semibold text-slate-900">질문별 AI 언급률</h3>
                    <p className="mt-1 text-xs text-slate-500">
                      환자 질문 변형 단위로 본 AI 언급률입니다. 보완 작업 우선순위를 정하는 보조 지표로 사용합니다.
                    </p>
                  </div>
                  <table className="w-full text-sm">
                    <thead className="border-b border-slate-200 bg-slate-50">
                      <tr>
                        <th className="px-6 py-3 text-left font-medium text-slate-600">환자 질문</th>
                        <th className="px-6 py-3 text-center font-medium text-slate-600">AI 언급률</th>
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
