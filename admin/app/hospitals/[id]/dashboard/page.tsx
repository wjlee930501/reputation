'use client'

import Link from 'next/link'
import { useEffect, useState } from 'react'
import { useParams } from 'next/navigation'
import { ApiError, fetchAPI } from '@/lib/api'
import { OperatorIssuePanel } from '@/app/_components/OperatorIssuePanel'
import { isExpectedOperatorRequestFailure, safeOperatorError } from '@/lib/operations-journey'
import { countUnpublishedCarriedOver } from '@/lib/content'
import { canRunMeasurement } from '@/lib/operator-safety'
import { formatActorLabel } from '@/lib/actor-display'
import { summarizeSovTrend, trimTrendToMeasuredWeeks } from '@/lib/sov-trend'
import {
  MENTION_RATE_EXCLUSION_COPY,
  MENTION_RATE_FAILURE_ALERT_COPY,
  describeMeasurementRunMentionRateImpact,
} from '@/lib/measurement-run-copy'
import {
  QUESTION_COUNT_LABELS,
  describeQuestionPhraseCounts,
  summarizeQuestionCounts,
} from '@/lib/question-counts'
import {
  EXPOSURE_ACTION_LIST_LIMIT,
  describeExposureActions,
  summarizeExposureActions,
} from '@/lib/exposure-action-counts'
import { useHospitalHeader } from '../hospital-context'
import {
  EXPOSURE_ACTION_STATUS_LABELS,
  EXPOSURE_ACTION_TYPE_LABELS,
  type AIQueryTarget,
  type ContentItem,
  type ExposureAction,
  type MeasurementRun,
  type OperationResponse,
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

// sov_pct·mention_rate는 nullable — 성공 측정 0건이면 null(측정 안 됨)이고,
// 0은 '측정했으나 언급되지 않음'이다. 화면에서 이 둘을 같은 숫자로 합치지 않는다.
interface TrendPoint {
  week_start: string
  sov_pct: number | null
  mention_count: number
  total_count: number
}

interface QueryPlatformBreakdown {
  platform_label?: string | null
  mention_count: number
  total_count: number
  failure_count: number
  mention_rate: number | null
}

interface QueryRow {
  query_id: string
  query_text: string
  mention_rate: number | null
  mention_count: number
  total_count: number
  failure_count?: number
  platform_breakdown?: Record<string, QueryPlatformBreakdown>
  last_measured_at: string | null
}

interface ReadinessCheck {
  key: string
  label: string
  passed: boolean
  weight: number
  next_action: string
  display?: {
    state_label?: string | null
  }
}

interface Readiness {
  score: number
  status: string
  display?: {
    status_label?: string | null
  }
  published_content_count: number
  sov_record_count: number
  report_count: number
  checks: ReadinessCheck[]
}

interface AuditLogRow {
  id: string
  hospital_id: string | null
  actor: string
  action: string
  target_type: string | null
  target_id: string | null
  detail: Record<string, unknown> | null
  created_at: string | null
}

const AUDIT_ACTION_LABELS: Record<string, string> = {
  trigger_v0_report: '초기 진단 리포트 다시 만들기',
  trigger_v0_report_requested: '초기 진단 리포트 다시 만들기 요청',
  auto_approve_philosophy: '운영 기준 자동 승인',
  auto_review_philosophy_escalated: '운영 기준 자동 검수 확인 필요',
  incident_occurrence_recorded: '운영 이상 기록',
  handoff_accepted: '고객 인수 승인',
  handoff_contracted: '계약 정보 저장',
  create_hospital: '병원 등록',
  set_schedule: '발행 스케줄 저장',
  profile_completed: '병원 기본 정보 완료',
  run_sov: 'AI 언급률 측정',
  rebuild_site: '사이트 재빌드',
  connect_domain: '커스텀 도메인 연결',
  disconnect_domain: '커스텀 도메인 연결 해제',
  provision_domain_certificate: 'HTTPS 인증서 발급',
  verify_domain: '공개 주소 확인',
  regenerate_content: '콘텐츠 재생성',
  publish_content: '콘텐츠 발행',
  reject_content: '콘텐츠 반려',
  approve_philosophy: '운영 기준 승인',
  update_exposure_action: 'AI 노출 작업 변경',
  upload_source_asset: '자료 업로드',
  crawl_source_url: 'URL 자동 크롤',
  exclude_source_asset: '자료 제외',
  reinclude_source_asset: '자료 제외 해제',
  toggle_source_public: '사진 공개 토글',
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
    OPENAI_CHAT_COMPLETIONS: 'OpenAI 모델 응답 측정',
    OPENAI_RESPONSES_WEB_SEARCH: 'ChatGPT Search 유사 측정',
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

function formatMeasurementFailurePlatforms(summary: Record<string, unknown> | null): string | null {
  const platforms = summary?.platforms
  if (!platforms || typeof platforms !== 'object' || Array.isArray(platforms)) return null
  const labels: Record<string, string> = { chatgpt: 'ChatGPT', gemini: 'Gemini' }
  const parts = Object.entries(platforms).flatMap(([platform, value]) => {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return []
    const counts = value as Record<string, unknown>
    const success = typeof counts.success_count === 'number' ? counts.success_count : 0
    const failure = typeof counts.failure_count === 'number' ? counts.failure_count : 0
    const attempted = typeof counts.attempted_count === 'number' ? counts.attempted_count : success + failure
    const planned = typeof counts.planned_count === 'number' ? counts.planned_count : attempted
    const skipped = typeof counts.skipped_count === 'number' ? counts.skipped_count : Math.max(0, planned - attempted)
    const interrupted = skipped > 0 ? ` · 공급자 장애로 ${skipped}건 호출 중단` : ''
    return [`${labels[platform] ?? platform} 성공 ${success}·실패 ${failure} (시도 ${attempted}/${planned})${interrupted}`]
  })
  return parts.length > 0 ? parts.join(' / ') : null
}

function getReadinessStatusLabel(readiness: Readiness | null): string {
  if (!readiness) return '측정 후 산출'
  return readiness.display?.status_label ?? (readiness.status === 'READY' ? '운영 준비 완료' : '보완 필요')
}

function getReadinessCheckStateLabel(check: ReadinessCheck): string {
  return check.display?.state_label ?? (check.passed ? '완료' : '필요')
}

function getExposureActionTypeLabel(action: ExposureAction) {
  const fallback = EXPOSURE_ACTION_TYPE_LABELS[action.action_type] ?? {
    label: '개선 작업 유형 확인 필요',
    color: 'bg-slate-50 text-slate-700 border-slate-200',
  }
  return { ...fallback, label: action.display?.action_type_label ?? fallback.label }
}

function getExposureActionStatusLabel(action: ExposureAction) {
  const fallback = EXPOSURE_ACTION_STATUS_LABELS[action.status] ?? {
    label: '처리 상태 확인 필요',
    color: 'bg-slate-50 text-slate-700 border-slate-200',
  }
  return { ...fallback, label: action.display?.status_label ?? fallback.label }
}

export default function DashboardPage() {
  const { id } = useParams<{ id: string }>()
  const { hospital } = useHospitalHeader()
  const [trendData, setTrendData] = useState<TrendPoint[]>([])
  const [queries, setQueries] = useState<QueryRow[]>([])
  const [readiness, setReadiness] = useState<Readiness | null>(null)
  const [measurementRuns, setMeasurementRuns] = useState<MeasurementRun[]>([])
  const [exposureActions, setExposureActions] = useState<ExposureAction[]>([])
  const [queryTargets, setQueryTargets] = useState<AIQueryTarget[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [operationLoading, setOperationLoading] = useState<string | null>(null)
  const [operationMessage, setOperationMessage] = useState<string | null>(null)
  const [operationError, setOperationError] = useState<string | null>(null)
  const [auditLogs, setAuditLogs] = useState<AuditLogRow[]>([])
  // 이번 달 전월 이월 콘텐츠 중 아직 발행되지 않은 슬롯 수 — 우선 발행 알림용
  const [carriedOverCount, setCarriedOverCount] = useState(0)

  const refreshAuditLogs = async () => {
    try {
      const rows = await fetchAPI(`/admin/hospitals/${id}/operations/audit-logs?limit=20`) as AuditLogRow[]
      setAuditLogs(Array.isArray(rows) ? rows : [])
    } catch {
      // Audit log 조회 실패는 운영 화면을 깨뜨리지 않도록 silent fail.
    }
  }

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    // 개별 호출 실패를 삼키지 않고 집계한다 — 성공한 데이터는 그대로 렌더링하되,
    // 하나라도 실패하면 상단에 부분 실패 배너를 보여준다.
    Promise.allSettled([
      fetchAPI<TrendPoint[]>(`/admin/hospitals/${id}/sov/trend`),
      fetchAPI<QueryRow[]>(`/admin/hospitals/${id}/sov/queries`),
      fetchAPI<Readiness | null>(`/admin/hospitals/${id}/readiness`),
      fetchAPI<MeasurementRun[]>(`/admin/hospitals/${id}/sov/measurement-runs`),
      // 보완 작업 화면과 같은 창을 봐야 두 화면의 숫자가 같다(A-6).
      fetchAPI<ExposureAction[]>(
        `/admin/hospitals/${id}/exposure-actions?limit=${EXPOSURE_ACTION_LIST_LIMIT}`,
      ),
      fetchAPI<AIQueryTarget[]>(`/admin/hospitals/${id}/query-targets`),
      fetchAPI<AuditLogRow[]>(`/admin/hospitals/${id}/operations/audit-logs?limit=20`),
    ])
      .then(([trend, qs, readinessData, runs, actions, targets, audit]) => {
        if (cancelled) return
        let failedCount = 0
        function unwrap<T>(result: PromiseSettledResult<T>, fallback: T): T {
          if (result.status === 'fulfilled') return result.value
          failedCount += 1
          return fallback
        }
        const trendValue = unwrap(trend, [] as TrendPoint[])
        const queriesValue = unwrap(qs, [] as QueryRow[])
        const readinessValue = unwrap(readinessData, null)
        const runsValue = unwrap(runs, [] as MeasurementRun[])
        const actionsValue = unwrap(actions, [] as ExposureAction[])
        const targetsValue = unwrap(targets, [] as AIQueryTarget[])
        const auditValue = unwrap(audit, [] as AuditLogRow[])

        setTrendData(Array.isArray(trendValue) ? trendValue : [])
        setQueries(Array.isArray(queriesValue) ? queriesValue : [])
        setReadiness(readinessValue)
        setMeasurementRuns(Array.isArray(runsValue) ? runsValue : [])
        setExposureActions(Array.isArray(actionsValue) ? actionsValue : [])
        setQueryTargets(Array.isArray(targetsValue) ? targetsValue : [])
        setAuditLogs(Array.isArray(auditValue) ? auditValue : [])

        if (failedCount > 0) {
          setError('일부 데이터를 불러오지 못했습니다. 표시된 수치가 불완전할 수 있으니 새로고침 후 다시 확인해 주세요.')
        }
      })
      .finally(() => { if (!cancelled) setLoading(false) })

    // 이월 알림은 보조 정보 — 가벼운 단건 조회로 가져오고, 실패해도 보드를 깨뜨리지 않는다.
    const now = new Date()
    fetchAPI<ContentItem[]>(`/admin/hospitals/${id}/content?year=${now.getFullYear()}&month=${now.getMonth() + 1}`)
      .then((contentItems) => {
        if (cancelled) return
        setCarriedOverCount(countUnpublishedCarriedOver(Array.isArray(contentItems) ? contentItems : []))
      })
      .catch(() => { /* silent — 이월 알림 없이 보드는 정상 동작 */ })

    return () => { cancelled = true }
  }, [id])

  // 측정이 시작되기 전의 주는 차트에서 잘라낸다 — 빈 칸이 측정 실패로 읽힌다(A-5).
  const measuredWeeks = trimTrendToMeasuredWeeks(trendData)
  const trendSummary = summarizeSovTrend(trendData)
  const currentSov = trendSummary.current
  const change = trendSummary.change
  const questionCounts = summarizeQuestionCounts(queryTargets, queries)
  const latestMeasurementRuns = measurementRuns.slice(0, 3)
  const topExposureActions = exposureActions.slice(0, 3)

  const activeTargets = queryTargets.filter((target) => target.status === 'ACTIVE')
  const lastRun = measurementRuns[0] ?? null
  const actionCounts = summarizeExposureActions(exposureActions)
  const blockedActionCount = actionCounts.blocked
  const failedMeasurementCount = measurementRuns.reduce((sum, run) => sum + run.failure_count, 0)
  const pendingChecks = readiness?.checks.filter((check) => !check.passed).slice(0, 2) ?? []

  const hasQueryTargets = activeTargets.length > 0
  const hasActiveVariant = canRunMeasurement(queryTargets)
  const canRunSov = Boolean(
    hospital
    && (hospital.status === 'ACTIVE' || hospital.status === 'PENDING_DOMAIN')
    && hasActiveVariant,
  )
  // V0는 한 번만 만든다 — 이미 완료된 병원은 백엔드가 재실행을 거절한다(409).
  // 버튼을 열어두면 눌러본 뒤에야 알게 되므로 미리 잠그고 이유를 보여준다.
  const v0AlreadyDone = Boolean(hospital?.v0_report_done)
  const hasMeasurement = measurementRuns.some(
    (run) => run.status === 'COMPLETED' || run.status === 'PARTIAL',
  )
  const hasExposureActions = exposureActions.length > 0
  const hasBrief = (readiness?.published_content_count ?? 0) > 0

  const queryTargetsHref = `/hospitals/${id}/query-targets`
  const exposureActionsHref = `/hospitals/${id}/exposure-actions`
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
            href: exposureActionsHref,
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

  // 추이 창(12주) 안에 성공 측정이 한 건도 없으면 차트 대신 안내를 띄운다.
  // 측정 전/전부 실패인 구간을 0% 선으로 그리면 '언급이 아예 없다'는 허위 신호가 된다.
  const isAnalyticsEmpty =
    !loading && !error && !measuredWeeks.some((point) => point.sov_pct !== null)

  async function runOperation(key: string, path: string) {
    setOperationLoading(key)
    setOperationMessage(null)
    setOperationError(null)
    try {
      const result = await fetchAPI(`/admin/hospitals/${id}/operations/${path}`, {
        method: 'POST',
      }) as OperationResponse
      if (path === 'verify-domain') {
        setOperationMessage(result.verified ? '공개 주소 연결 확인 완료' : '공개 주소 연결 확인 필요')
      } else if (path === 'trigger-v0-report') {
        const runId = result.operation_run_id ? ` 작업 ${result.operation_run_id}` : ''
        if (result.idempotent_replay || result.operation_state === 'QUEUED' || result.operation_state === 'RUNNING') {
          setOperationMessage(`초기 진단이 진행 중입니다.${runId}`)
        } else {
          setOperationMessage(`초기 진단 리포트를 다시 만들기 시작했습니다.${runId}`)
        }
      } else if (path === 'run-sov') {
        setOperationMessage('AI 답변 언급 측정을 접수했습니다. 진행 상태에서 결과를 확인하세요.')
      } else if (path === 'rebuild-site') {
        setOperationMessage('공개 정보 갱신을 접수했습니다. 진행 상태에서 결과를 확인하세요.')
      }
      await refreshAuditLogs()
    } catch (e: unknown) {
      if (!isExpectedOperatorRequestFailure(e)) throw e
      const apiMessage = e instanceof ApiError ? e.message.trim() : ''
      setOperationError(
        apiMessage
          || safeOperatorError('operations', '현재 상태를 다시 확인한 뒤 같은 작업 버튼을 다시 누르세요.'),
      )
    } finally {
      setOperationLoading(null)
    }
  }

  function sourceTypeLabel(value: string | null): string | null {
    if (!value) return null
    const raw = value.replace(/^SourceType\./, '')
    const labels: Record<string, string> = {
      YOUTUBE: '유튜브', HOMEPAGE: '홈페이지', NAVER_BLOG: '네이버 블로그', INTERVIEW: '원장 인터뷰',
      BROCHURE: '브로슈어', LANDING_PAGE: '랜딩 페이지', INTERNAL_NOTE: '내부 메모', OTHER: '기타',
    }
    return labels[raw] ?? raw
  }

  function sourceStatusLabel(value: string | null): string | null {
    if (!value) return null
    const raw = value.replace(/^SourceStatus\./, '')
    const labels: Record<string, string> = {
      PENDING: '대기', PROCESSED: '처리 완료', EXCLUDED: '제외', ERROR: '오류',
    }
    return labels[raw] ?? raw
  }

  function formatAuditAction(action: string): string {
    return AUDIT_ACTION_LABELS[action] ?? action
  }

  function formatAuditDetail(action: string, detail: Record<string, unknown> | null): string {
    if (!detail) return ''
    if (action === 'verify_domain') {
      const verified = detail.verified === true
      return verified ? '공개 주소 연결 확인 완료' : '공개 주소 연결 확인 필요'
    }
    if (action === 'update_exposure_action' && detail.changes && typeof detail.changes === 'object') {
      const changes = Object.keys(detail.changes as Record<string, unknown>)
      return `변경 필드: ${changes.join(', ') || '-'}`
    }
    if (action === 'publish_content' && typeof detail.title === 'string') {
      return `발행: ${detail.title}`
    }
    if (action === 'reject_content' && typeof detail.previous_title === 'string') {
      return `반려: ${detail.previous_title}`
    }
    if (action === 'approve_philosophy' && typeof detail.version !== 'undefined') {
      return `버전 ${detail.version} 승인 (근거 검토 확인)`
    }
    if (action === 'upload_source_asset') {
      const sizeBytes = typeof detail.size_bytes === 'number' ? detail.size_bytes : null
      const extractor = typeof detail.extractor === 'string' ? detail.extractor : null
      const sourceType = typeof detail.source_type === 'string' ? detail.source_type : null
      const parts = [sourceType, extractor, sizeBytes ? `${(sizeBytes / 1024).toFixed(0)}KB` : null]
      return parts.filter(Boolean).join(' · ')
    }
    if (action === 'crawl_source_url') {
      const url = typeof detail.url === 'string' ? detail.url : null
      const chars = typeof detail.extracted_chars === 'number' ? `${detail.extracted_chars}자` : null
      return [url, chars].filter(Boolean).join(' · ')
    }
    if (action === 'exclude_source_asset') {
      const fromStatus = typeof detail.from_status === 'string' ? detail.from_status : null
      const sourceType = typeof detail.source_type === 'string' ? detail.source_type : null
      return [sourceTypeLabel(sourceType), fromStatus ? `이전 상태: ${sourceStatusLabel(fromStatus)}` : null].filter(Boolean).join(' · ')
    }
    if (action === 'reinclude_source_asset') {
      const toStatus = typeof detail.to_status === 'string' ? detail.to_status : null
      const sourceType = typeof detail.source_type === 'string' ? detail.source_type : null
      const notes = typeof detail.restored_note_count === 'number' ? `근거 노트 ${detail.restored_note_count}개 복원` : null
      return [sourceTypeLabel(sourceType), toStatus ? `복원 상태: ${sourceStatusLabel(toStatus)}` : null, notes].filter(Boolean).join(' · ')
    }
    if (action === 'toggle_source_public') {
      if (detail.from === false && detail.to === true) return '비공개 → 공개'
      if (detail.from === true && detail.to === false) return '공개 → 비공개'
      return ''
    }
    return ''
  }

  return (
    <main className="min-h-full space-y-6 bg-[var(--color-revisit-background-user)] p-4 sm:p-6 lg:p-8">
      {/* Hero */}
      <section className="rounded-2xl border border-[var(--color-revisit-coolgrey-20)] bg-[var(--color-revisit-nav)] p-7 text-white">
        <p className="details2 font-semibold uppercase text-[var(--color-revisit-primary-80)]">
          AI 노출 운영
        </p>
        <div className="mt-2 flex flex-col gap-5 lg:flex-row lg:items-end lg:justify-between">
          <div className="max-w-2xl">
            <h2 className="heading2">AI 노출 운영 보드</h2>
            <p className="body4 mt-2 text-[var(--color-revisit-coolgrey-85)]">
              환자 질문 정의 → AI 언급률 측정 → 부족한 부분 진단·보완 작업 → 환자 질문에 맞춘 콘텐츠 가이드 작성을
              한 화면에서 운영합니다. AI가 우리 병원을 정확히 이해하고 추천 후보에 올리도록 정보 구조를 다듬는
              내부 콘솔이며, 노출을 보장하는 게 아니라 개선과 재측정을 반복하는 흐름을 관리합니다.
            </p>
          </div>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:min-w-[440px]">
            <HeroStat
              label={QUESTION_COUNT_LABELS.topicsOperating}
              value={`${questionCounts.topicsOperating}개`}
              hint={describeQuestionPhraseCounts(questionCounts)}
            />
            <HeroStat
              label="현재 AI 언급률"
              value={currentSov !== null ? `${currentSov.toFixed(1)}%` : '-'}
              hint={trendSummary.hint}
              tone={change === null ? 'neutral' : change >= 0 ? 'up' : 'down'}
            />
            <HeroStat
              label="남은 보완 작업"
              value={`${actionCounts.active}건`}
              hint={describeExposureActions(actionCounts)}
            />
            <HeroStat
              label="AI 노출 준비도"
              value={readiness ? String(readiness.score) : '-'}
              hint={readiness ? getReadinessStatusLabel(readiness) : '측정 후 산출'}
            />
          </div>
        </div>

        <div className="mt-5 flex flex-wrap items-center gap-3">
          <span className="details2 uppercase text-[var(--color-revisit-primary-80)]">다음 단계</span>
          <Link
            href={nextStep.href}
            className="inline-flex min-h-9 items-center gap-1.5 rounded-lg border border-white bg-white px-4 py-1.5 text-xs font-semibold text-[var(--color-revisit-nav)] transition-colors hover:bg-[var(--color-revisit-primary-95)]"
          >
            {nextStep.label}
            <span aria-hidden>→</span>
          </Link>
          <span className="details2 text-[var(--color-revisit-coolgrey-80)]">{nextStep.hint}</span>
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

      {!loading && (
        <section className="grid gap-4 lg:grid-cols-[1.15fr_0.85fr]">
          <div className="admin-panel p-6">
            <div className="flex items-start justify-between gap-4">
              <div>
                <p className="admin-eyebrow">원장 보고 요약</p>
                <h3 className="title1 mt-1 text-[var(--color-revisit-text-title)]">이번 달 먼저 볼 운영 요약</h3>
                <p className="body4 admin-muted mt-1">
                  세부 측정 로그보다 원장님께 설명할 변화와 다음 조치를 먼저 확인합니다.
                </p>
              </div>
              <span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-medium text-slate-600">
                {lastRun ? formatDateTime(lastRun.completed_at ?? lastRun.started_at) : '첫 측정 전'}
              </span>
            </div>
            <div className="mt-5 grid gap-3 md:grid-cols-3">
              <FocusCard
                label="현재 AI 언급률"
                value={currentSov !== null ? `${currentSov.toFixed(1)}%` : '-'}
                hint={trendSummary.hint}
                tone={change === null ? 'neutral' : change >= 0 ? 'good' : 'warn'}
              />
              <FocusCard
                label="근거 기반 콘텐츠 상태"
                value={`${readiness?.published_content_count ?? 0}편`}
                hint={readiness ? `운영 준비도 ${readiness.score}/100` : '운영 기준 승인 후 발행'}
                tone={(readiness?.published_content_count ?? 0) > 0 ? 'good' : 'neutral'}
              />
              <FocusCard
                label="다음 액션"
                value={nextStep.label}
                hint={nextStep.hint}
                tone={blockedActionCount > 0 ? 'warn' : 'neutral'}
                href={nextStep.href}
              />
            </div>
          </div>

          <div className="admin-panel p-6">
            <h3 className="title3 text-[var(--color-revisit-text-title)]">확인 필요한 항목</h3>
            <p className="body4 admin-muted mt-1">
              운영자가 오늘 처리해야 할 위험 신호만 추려 보여줍니다.
            </p>
            <div className="mt-4 space-y-3">
              {carriedOverCount > 0 && (
                <Link href={contentHref} className="block">
                  <AlertLine
                    tone="warn"
                    label={`전월 이월 콘텐츠 ${carriedOverCount}건 — 우선 발행 필요`}
                    hint="반려로 다음 달로 넘어온 콘텐츠입니다. 콘텐츠 탭에서 가장 먼저 검토·발행해 주세요."
                  />
                </Link>
              )}
              {blockedActionCount > 0 && (
                <AlertLine tone="warn" label={`막힌 보완 작업 ${blockedActionCount}건`} hint="담당자 확인 또는 자료 보강이 필요합니다." />
              )}
              {failedMeasurementCount > 0 && (
                <AlertLine tone="warn" label={`AI 확인 실패 누적 ${failedMeasurementCount}건`} hint={MENTION_RATE_FAILURE_ALERT_COPY} />
              )}
              {pendingChecks.map((check) => (
                <AlertLine key={check.key} tone="neutral" label={check.label} hint={check.next_action} />
              ))}
              {carriedOverCount === 0 && blockedActionCount === 0 && failedMeasurementCount === 0 && pendingChecks.length === 0 && (
                <AlertLine tone="good" label="큰 확인 항목 없음" hint="현재는 다음 액션 중심으로 운영을 이어가면 됩니다." />
              )}
            </div>
          </div>
        </section>
      )}

      {!loading && (
        <section className="admin-panel p-5">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
            <div>
              <p className="admin-eyebrow">v1.0 운영 제어</p>
              <h3 className="title3 mt-1 text-[var(--color-revisit-text-title)]">수동 재실행·상태 확인</h3>
              <p className="body4 admin-muted mt-1">
                고객 보고 전 필요한 분석, 공개 정보 갱신, 공개 주소 확인을 이 화면에서 다시 실행합니다. 모든 실행은 감사 기록에 남습니다.
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              <OperationButton
                label="초기 진단 리포트 다시 만들기"
                loading={operationLoading === 'v0'}
                disabled={v0AlreadyDone}
                onClick={() => runOperation('v0', 'trigger-v0-report')}
              />
              <OperationButton
                label="AI 언급률 측정"
                loading={operationLoading === 'sov'}
                disabled={!canRunSov}
                onClick={() => runOperation('sov', 'run-sov')}
              />
              <OperationButton
                label="사이트 재빌드"
                loading={operationLoading === 'site'}
                onClick={() => runOperation('site', 'rebuild-site')}
              />
              <OperationButton
                label="공개 주소 확인"
                loading={operationLoading === 'dns'}
                onClick={() => runOperation('dns', 'verify-domain')}
              />
            </div>
          </div>
          {v0AlreadyDone && (
            <p className="mt-3 text-xs text-slate-600">
              초기 진단 리포트는 이미 생성됐습니다. 초기 진단은 병원당 한 번만 만들며, 이후 수치는
              &lsquo;AI 언급률 측정&rsquo;과 월간 리포트로 확인합니다.
            </p>
          )}
          {!canRunSov && (
            <p className="mt-3 text-xs text-amber-700">
              AI 언급률 측정은 운영 중 또는 공개 주소 확인 대기 상태에서, 활성 환자 질문 문구가 있을 때 실행할 수 있습니다.
            </p>
          )}
          {operationMessage && (
            <div className="mt-3 rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-700">
              {operationMessage}
            </div>
          )}
          {operationError && (
            <div className="mt-3"><OperatorIssuePanel message={operationError} surface="operations" /></div>
          )}
        </section>
      )}

      {/* Audit log — Admin actions trail */}
      {!loading && (
        <details className="admin-disclosure">
          <summary>
            <div>
              <p className="admin-eyebrow">감사 로그</p>
              <h3 className="title3 mt-1 text-[var(--color-revisit-text-title)]">최근 운영 액션 기록</h3>
            </div>
          </summary>
          <div className="p-5 pt-2">
          <p className="body4 admin-muted">
            고객 영향이 있는 모든 운영 액션은 이 로그에 남습니다. 실행자(actor)는 환경 변수 ADMIN_ACTOR_NAME 기준입니다.
          </p>
          {auditLogs.length === 0 ? (
            <p className="mt-4 rounded-xl border border-dashed border-slate-200 bg-slate-50 px-4 py-6 text-center text-sm text-slate-500">
              아직 기록된 운영 액션이 없습니다.
            </p>
          ) : (
            <ol className="mt-4 divide-y divide-slate-100">
              {auditLogs.map((log) => (
                <li key={log.id} className="grid gap-1 py-3 md:grid-cols-[160px_1fr_180px] md:items-center">
                  <span className="text-xs text-slate-500">{formatDateTime(log.created_at)}</span>
                  <div>
                    <p className="text-sm font-medium text-slate-900">{formatAuditAction(log.action)}</p>
                    {formatAuditDetail(log.action, log.detail) && (
                      <p className="mt-0.5 text-xs text-slate-500">{formatAuditDetail(log.action, log.detail)}</p>
                    )}
                  </div>
                  <span className="rounded-full border border-slate-200 bg-slate-50 px-3 py-1 text-xs font-medium text-slate-600 md:justify-self-end">
                    {formatActorLabel(log.actor)}
                  </span>
                </li>
              ))}
            </ol>
          )}
          </div>
        </details>
      )}

      {/* Workflow strip */}
      {!loading && (
        <section className="admin-panel p-5">
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
                  ? `${activeTargets.length}개 운영 중`
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
                  ? `최근 ${lastRun.display?.status_label ?? formatRunStatus(lastRun.status)} · ${formatDateTime(
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
              summary={hasExposureActions ? describeExposureActions(actionCounts) : '진단 결과가 아직 없습니다.'}
              href={exposureActionsHref}
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
        <details id="v0-measurement-runs" className="admin-disclosure scroll-mt-6" open>
          <summary>
            <div>
              <h3 className="text-base font-semibold text-slate-900">측정 실행 로그</h3>
            </div>
          </summary>
          <div className="p-5 pt-2">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
            <p className="text-sm text-slate-500">{MENTION_RATE_EXCLUSION_COPY}</p>
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
                      <RunStatusPill status={run.status} label={run.display?.status_label} />
                    </p>
                    <p className="mt-1 text-xs text-slate-500">
                      측정 방식: {run.display?.measurement_method_label ?? formatMeasurementMethod(run.measurement_method)}
                      {run.search_mode === 'model' && (
                        <span className="ml-1 text-amber-600">(웹 검색 미사용)</span>
                      )}
                    </p>
                  </div>
                  <div className="text-sm text-slate-700">
                    <p>
                      성공 {run.success_count}/{run.query_count} · 실패 {run.failure_count}
                    </p>
                    <p className="mt-1 text-xs text-slate-500">
                      {describeMeasurementRunMentionRateImpact(run)}
                    </p>
                    {typeof run.error_summary?.safe_error_message === 'string' && (
                      <p className="mt-1 text-xs font-medium text-amber-700">
                        {run.error_summary.safe_error_message}
                      </p>
                    )}
                    {formatMeasurementFailurePlatforms(run.error_summary) && (
                      <p className="mt-1 text-xs text-amber-700">
                        {formatMeasurementFailurePlatforms(run.error_summary)}
                      </p>
                    )}
                  </div>
                  <div className="text-xs text-slate-500 md:text-right">
                    <p>시작 {formatDateTime(run.started_at)}</p>
                    <p className="mt-1">완료 {formatDateTime(run.completed_at)}</p>
                  </div>
                </div>
              ))}
            </div>
          )}
          </div>
        </details>
      )}

      {/* Top AI exposure work queue items */}
      {!loading && (
        <section className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
          <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
            <div>
              <h3 className="text-base font-semibold text-slate-900">이번 달 AI 노출 개선 TOP 3</h3>
              <p className="mt-1 text-sm text-slate-500">
                환자 질문별 AI 언급률 진단에서 우선순위가 높은 보완 작업을 표시합니다. 상세 편집은 AI 노출 개선 작업 화면에서 진행합니다.
              </p>
            </div>
            <Link
              href={exposureActionsHref}
              className="self-start rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50"
            >
              작업 전체 보기 →
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
              ctaHref={hasMeasurement ? exposureActionsHref : queryTargetsHref}
            />
          ) : (
            <div className="mt-4 grid gap-3">
              {topExposureActions.map((action) => {
                const actionType = getExposureActionTypeLabel(action)
                const actionStatus = getExposureActionStatusLabel(action)
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
                        <p className="mt-2 text-sm font-semibold text-slate-900">
                          {action.title}
                        </p>
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
                    AI가 참고할 수 있는 병원 기본 정보, 구글 지도·프로필, 공개 콘텐츠, 환자 질문 측정 데이터를 기준으로 계산합니다.
                  </p>
                </div>
                <div className="text-right">
                  <p className="text-3xl font-bold text-slate-900">{readiness.score}</p>
                  <p className="text-xs text-slate-400">{getReadinessStatusLabel(readiness)}</p>
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
                      {getReadinessCheckStateLabel(check)} · {check.label}
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
            <div className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
              <div className="flex items-center justify-between">
                <h3 className="text-base font-semibold text-slate-900">AI 언급률 주간 추이</h3>
                <span className="text-xs text-slate-400">
                  {QUESTION_COUNT_LABELS.phrasesMeasured} {questionCounts.phrasesMeasured}개 · 측정 시작 후 {measuredWeeks.length}주
                </span>
              </div>
              <div className="mt-4">
                <ResponsiveContainer width="100%" height={280}>
                  <LineChart
                    data={measuredWeeks}
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
                    {/*
                      connectNulls를 쓰면 미측정 주를 선으로 이어 그려 결측이 사라진 것처럼 보인다.
                      공백은 공백으로 남기고, 앞뒤가 비어 홀로 남은 측정 주도 보이도록 점을 찍는다.
                    */}
                    <Line
                      dataKey="sov_pct"
                      stroke="#1A4B8C"
                      strokeWidth={2}
                      name="AI 언급률"
                      dot={{ r: 3 }}
                      connectNulls={false}
                    />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </div>
          )}

          {/* 추이가 비어 있어도 질문별 표는 남긴다 — '측정 실패'가 어느 질문에서 났는지 확인해야 한다. */}
          {queries.length > 0 && (
            <div className="admin-responsive-table-wrap overflow-hidden rounded-2xl border border-slate-200 bg-white">
              <div className="border-b border-slate-100 px-6 py-4">
                <h3 className="text-base font-semibold text-slate-900">질문별 AI 언급률</h3>
                <p className="mt-1 text-xs text-slate-500">
                  환자 질문 문구 단위로 본 AI 언급률입니다. 보완 작업 우선순위를 정하는 보조 지표로 사용합니다.
                </p>
              </div>
              <table className="admin-responsive-table w-full text-sm">
                <thead className="border-b border-slate-200 bg-slate-50">
                  <tr>
                    <th className="px-6 py-3 text-left font-medium text-slate-600">환자 질문</th>
                    <th className="px-6 py-3 text-center font-medium text-slate-600">AI 언급률</th>
                    <th className="px-6 py-3 text-left font-medium text-slate-600">서비스별 확인 결과</th>
                    <th className="px-6 py-3 text-center font-medium text-slate-600">최근 측정</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {queries.map((q) => (
                    <tr key={q.query_id} className="transition-colors hover:bg-slate-50">
                      <td className="px-6 py-3 text-slate-700" data-primary="true">{q.query_text}</td>
                      <td className="px-6 py-3 text-center" data-label="AI 언급률">
                        {/* null은 측정 자체가 안 된 상태 — 0%로 찍으면 '언급 안 됨'이라는 오진이 된다. */}
                        <span
                          className={`font-medium ${
                            q.mention_rate !== null && q.mention_rate >= 50
                              ? 'text-emerald-600'
                              : 'text-slate-500'
                          }`}
                        >
                          {q.mention_rate !== null
                            ? `${q.mention_rate.toFixed(0)}%`
                            : q.failure_count
                              ? '측정 실패'
                              : '측정 대기'}
                        </span>
                        <p className="mt-1 text-[11px] text-slate-400">
                          {q.total_count}회 확인 중 {q.mention_count}회 언급
                          {q.failure_count ? ` · 확인 실패 ${q.failure_count}건` : ''}
                        </p>
                      </td>
                      <td className="px-6 py-3" data-label="서비스별">
                        <PlatformBreakdown value={q.platform_breakdown} />
                      </td>
                      <td className="px-6 py-3 text-center text-xs text-slate-400" data-label="최근 측정">
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
        </section>
      )}
    </main>
  )
}


function PlatformBreakdown({ value }: { value?: Record<string, QueryPlatformBreakdown> }) {
  const entries = Object.entries(value ?? {})
  if (entries.length === 0) {
    return <span className="text-xs text-slate-400">서비스별 결과 대기</span>
  }

  return (
    <div className="flex flex-wrap gap-1.5">
      {entries.map(([platform, row]) => (
        <span
          key={platform}
          className="inline-flex items-center gap-1 rounded-full border border-slate-200 bg-slate-50 px-2 py-1 text-[11px] text-slate-600"
        >
          <strong className="font-semibold text-slate-700">{row.platform_label ?? formatPlatformLabel(platform)}</strong>
          {/* 해당 서비스 측정이 전부 실패하면 null — 0%가 아니라 실패로 표기한다. */}
          <span>{row.mention_rate !== null ? `${row.mention_rate.toFixed(0)}%` : '측정 실패'}</span>
          <span className="text-slate-400">
            ({row.total_count}회 중 {row.mention_count}회)
          </span>
          {row.failure_count > 0 && <span className="text-amber-600">확인 실패 {row.failure_count}건</span>}
        </span>
      ))}
    </div>
  )
}

function OperationButton({
  label,
  loading,
  disabled = false,
  onClick,
}: {
  label: string
  loading: boolean
  disabled?: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={loading || disabled}
      className="admin-button px-3 py-2 disabled:cursor-not-allowed disabled:opacity-60"
    >
      {loading ? '실행 중...' : label}
    </button>
  )
}

function formatPlatformLabel(platform: string) {
  const labels: Record<string, string> = {
    CHATGPT: 'ChatGPT',
    GEMINI: 'Gemini',
    GOOGLE_AI: 'Google AI',
  }

  return labels[platform] ?? platform
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
      ? 'text-[var(--color-revisit-green-50)]'
      : tone === 'down'
        ? 'text-[var(--color-revisit-red-50)]'
        : 'text-[var(--color-revisit-coolgrey-80)]'
  return (
    <div className="rounded-xl border border-[rgba(255,255,255,0.18)] bg-[rgba(255,255,255,0.06)] px-4 py-3">
      <p className="details3 text-[var(--color-revisit-coolgrey-80)]">{label}</p>
      <p className="mt-1 text-xl font-bold text-white">{value}</p>
      {hint && <p className={`mt-1 text-[11px] ${toneClass}`}>{hint}</p>}
    </div>
  )
}

function FocusCard({
  label,
  value,
  hint,
  tone,
  href,
}: {
  label: string
  value: string
  hint: string
  tone: 'good' | 'warn' | 'neutral'
  href?: string
}) {
  const toneCls =
    tone === 'good'
      ? 'border-emerald-200 bg-emerald-50 text-emerald-900'
      : tone === 'warn'
        ? 'border-amber-200 bg-amber-50 text-amber-900'
        : 'border-slate-200 bg-slate-50 text-slate-900'
  const body = (
    <div className={`h-full rounded-xl border p-4 ${toneCls}`}>
      <p className="text-xs font-medium opacity-75">{label}</p>
      <p className="mt-1 text-xl font-bold leading-tight">{value}</p>
      <p className="mt-2 text-xs leading-5 opacity-75">{hint}</p>
    </div>
  )

  return href ? <Link href={href}>{body}</Link> : body
}

function AlertLine({
  tone,
  label,
  hint,
}: {
  tone: 'good' | 'warn' | 'neutral'
  label: string
  hint: string
}) {
  const cls =
    tone === 'good'
      ? 'bg-emerald-50 text-emerald-800 border-emerald-200'
      : tone === 'warn'
        ? 'bg-amber-50 text-amber-900 border-amber-200'
        : 'bg-slate-50 text-slate-800 border-slate-200'
  return (
    <div className={`rounded-xl border px-3 py-2 ${cls}`}>
      <p className="text-sm font-semibold">{label}</p>
      <p className="mt-0.5 text-xs leading-5 opacity-75">{hint}</p>
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

function RunStatusPill({ status, label }: { status: string; label?: string | null }) {
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
      {label ?? formatRunStatus(status)}
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
