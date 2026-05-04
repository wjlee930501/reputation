'use client'

import { KeyboardEvent, useCallback, useEffect, useMemo, useState } from 'react'
import { useParams } from 'next/navigation'
import { fetchAPI } from '@/lib/api'
import {
  EXPOSURE_ACTION_STATUS_LABELS,
  EXPOSURE_ACTION_TYPE_LABELS,
  ExposureAction,
  ExposureActionContentSummary,
  ExposureActionCreateBriefResponse,
  ExposureActionStatus,
  ExposureActionType,
  QUERY_TARGET_PRIORITY_LABELS,
  QUERY_TARGET_STATUS_LABELS,
  TYPE_LABELS,
} from '@/types'

const STATUS_OPTIONS: ExposureActionStatus[] = [
  'OPEN',
  'IN_PROGRESS',
  'BLOCKED',
  'COMPLETED',
  'CANCELLED',
  'ARCHIVED',
]

const BRIEF_CAPABLE_ACTION_TYPES = new Set<ExposureActionType>(['CONTENT', 'WEBBLOG_IA', 'SOURCE'])

const SEVERITY_LABELS: Record<string, { label: string; color: string }> = {
  CRITICAL: { label: '심각', color: 'bg-red-50 text-red-700 border-red-200' },
  HIGH: { label: '높음', color: 'bg-orange-50 text-orange-700 border-orange-200' },
  MEDIUM: { label: '중간', color: 'bg-amber-50 text-amber-700 border-amber-200' },
  LOW: { label: '낮음', color: 'bg-slate-50 text-slate-600 border-slate-200' },
}

const GAP_TYPE_LABELS: Record<string, string> = {
  NO_SUCCESSFUL_MEASUREMENT: '측정값 없음',
  MISSING_MENTION: '병원 미언급',
  LOW_MENTION_RATE: '낮은 AI 언급률',
  MENTIONS_COMPETITOR_ONLY: '경쟁 병원만 언급',
  COMPETITOR_VISIBILITY: '경쟁 병원이 더 많이 노출',
  COMPETITOR_DOMINANCE: '경쟁 병원이 더 많이 노출',
  NO_PUBLIC_CONTENT: '대응 콘텐츠 없음',
  WEAK_ENTITY_FACTS: '병원 기본 정보 부족',
  TECHNICAL_CRAWL_GAP: '크롤링/색인 보강',
  SOURCE_GAP: 'AI가 참고할 근거 자료 부족',
  SOURCE_SIGNAL_GAP: 'AI가 참고할 근거 신호 부족',
  SOURCE_AUTHORITY_GAP: '근거 자료의 권위 부족',
  CONTENT_STALE: '콘텐츠 신선도 낮음',
  MEDICAL_RISK_BLOCKED: '의료광고 리스크 차단',
}

const EVIDENCE_KEY_LABELS: Record<string, string> = {
  share_of_voice: 'AI 언급률',
  sov: 'AI 언급률',
  sov_pct: 'AI 언급률',
  sov_percent: 'AI 언급률',
  mention_rate: 'AI 언급률',
  mentioned_rate: 'AI 언급률',
  mentioned_count: '언급 횟수',
  mention_count: '언급 횟수',
  successful_count: '성공 측정 수',
  success_count: '성공 측정 수',
  failed_count: '실패 측정 수',
  total_count: '전체 측정 수',
  total_queries: '전체 질문 수',
  query_count: '질문 수',
  measured_count: '측정 수',
  competitor_names: '경쟁 병원',
  competitors: '경쟁 병원',
  competitor: '경쟁 병원',
  competitor_share: '경쟁 점유율',
  competitor_mentions: '경쟁 병원 언급',
  competitor_mention_rate: '경쟁 병원 언급률',
  missing_topics: '누락 토픽',
  topics: '토픽',
  keyword: '키워드',
  keywords: '키워드',
  query: '환자 질문',
  query_text: '환자 질문',
  query_name: '환자 질문',
  query_target: '환자 질문',
  query_target_name: '환자 질문',
  target_priority: '질문 우선순위',
  rule: '진단 규칙',
  ai_platform: 'AI 답변 서비스',
  platform: 'AI 답변 서비스',
  platforms: 'AI 답변 서비스',
  source_count: '참고 자료 수',
  source_total: '참고 자료 수',
  sources: '참고 자료',
  source_urls: '참고 URL',
  source_types: '참고 자료 유형',
  authority_score: '권위 점수',
  freshness_days: '경과 일수',
  last_published_at: '최근 발행',
  last_measured_at: '최근 측정',
  measured_at: '측정 시각',
  observed_at: '관측 시각',
  severity: '심각도',
  threshold: '임계값',
  gap_id: '진단 ID',
  reason: '사유',
  note: '메모',
  notes: '메모',
  message: '메시지',
}

const EVIDENCE_VALUE_LABELS: Record<string, string> = {
  chatgpt: 'ChatGPT',
  gemini: 'Gemini',
  claude: 'Claude',
  positive: '긍정',
  neutral: '중립',
  negative: '부정',
  no_successful_measurements: '성공 측정 없음',
  missing_mention: '병원 미언급',
  competitor_visibility: '경쟁 병원이 더 많이 노출',
  source_signal_gap: 'AI가 참고할 근거 자료 부족',
  HIGH: '높음',
  NORMAL: '보통',
  LOW: '낮음',
  high: '높음',
  normal: '보통',
  low: '낮음',
}

const PERCENT_KEY_RE = /(rate|share_of_voice|sov|percent|pct)/i

const ACTION_LIST_LIMIT = 20

interface BriefResultState {
  actionId: string
  contentItem: ExposureActionContentSummary
  philosophyGate: ExposureActionCreateBriefResponse['philosophy_gate']
}

export default function ExposureActionsPage() {
  const params = useParams<{ id: string }>()
  const hospitalId = params.id

  const [actions, setActions] = useState<ExposureAction[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)

  const [savingField, setSavingField] = useState<string | null>(null)
  const [saveMessage, setSaveMessage] = useState<{ kind: 'success' | 'error'; text: string } | null>(null)

  const [creatingBriefId, setCreatingBriefId] = useState<string | null>(null)
  const [briefResult, setBriefResult] = useState<BriefResultState | null>(null)

  const [ownerDraft, setOwnerDraft] = useState('')
  const [dueMonthDraft, setDueMonthDraft] = useState('')

  const selected = useMemo(
    () => actions.find((action) => action.id === selectedId) ?? null,
    [actions, selectedId],
  )

  useEffect(() => {
    if (selected) {
      setOwnerDraft(selected.owner ?? '')
      setDueMonthDraft(selected.due_month ?? '')
    } else {
      setOwnerDraft('')
      setDueMonthDraft('')
    }
  }, [selected])

  const loadActions = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data: ExposureAction[] = await fetchAPI(
        `/admin/hospitals/${hospitalId}/exposure-actions?limit=${ACTION_LIST_LIMIT}`,
      )
      const next = data ?? []
      setActions(next)
      setSelectedId((prev) => {
        if (prev && next.some((action) => action.id === prev)) return prev
        return next[0]?.id ?? null
      })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'AI 노출 보완 작업을 불러오지 못했습니다.')
    } finally {
      setLoading(false)
    }
  }, [hospitalId])

  useEffect(() => {
    loadActions()
  }, [loadActions])

  function pushSaveMessage(kind: 'success' | 'error', text: string) {
    setSaveMessage({ kind, text })
    if (typeof window !== 'undefined') {
      window.setTimeout(() => {
        setSaveMessage((prev) => (prev && prev.text === text ? null : prev))
      }, 4000)
    }
  }

  async function patchAction(action: ExposureAction, patch: Record<string, unknown>, fieldKey: string) {
    setSavingField(fieldKey)
    setSaveMessage(null)
    try {
      const updated: ExposureAction = await fetchAPI(
        `/admin/hospitals/${hospitalId}/exposure-actions/${action.id}`,
        {
          method: 'PATCH',
          body: JSON.stringify(patch),
        },
      )
      setActions((prev) => prev.map((item) => (item.id === updated.id ? updated : item)))
      pushSaveMessage('success', '저장되었습니다.')
    } catch (err) {
      pushSaveMessage('error', err instanceof Error ? err.message : '저장하지 못했습니다.')
    } finally {
      setSavingField(null)
    }
  }

  async function handleCreateBrief(action: ExposureAction) {
    setCreatingBriefId(action.id)
    setSaveMessage(null)
    try {
      const data: ExposureActionCreateBriefResponse = await fetchAPI(
        `/admin/hospitals/${hospitalId}/exposure-actions/${action.id}/create-brief`,
        {
          method: 'POST',
          body: JSON.stringify({}),
        },
      )
      setActions((prev) => prev.map((item) => (item.id === data.action.id ? data.action : item)))
      setBriefResult({
        actionId: data.action.id,
        contentItem: data.content_item,
        philosophyGate: data.philosophy_gate,
      })
      pushSaveMessage('success', '콘텐츠 가이드가 생성되었습니다.')
    } catch (err) {
      pushSaveMessage('error', err instanceof Error ? err.message : '콘텐츠 가이드 생성에 실패했습니다.')
    } finally {
      setCreatingBriefId(null)
    }
  }

  function handleRowKeyDown(event: KeyboardEvent<HTMLLIElement>, actionId: string) {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      setSelectedId(actionId)
    }
  }

  const counts = useMemo(() => {
    const open = actions.filter((a) => a.status === 'OPEN').length
    const inProgress = actions.filter((a) => a.status === 'IN_PROGRESS').length
    const blocked = actions.filter((a) => a.status === 'BLOCKED').length
    const completed = actions.filter((a) => a.status === 'COMPLETED').length
    return { open, inProgress, blocked, completed }
  }, [actions])

  return (
    <main className="p-8 space-y-6 bg-slate-50 min-h-full">
      <section className="rounded-2xl bg-gradient-to-br from-slate-900 via-slate-800 to-blue-900 p-7 text-white shadow-sm">
        <p className="text-[11px] font-semibold uppercase tracking-[0.2em] text-blue-200">
          Exposure Action Work Queue
        </p>
        <div className="mt-2 flex flex-col gap-5 lg:flex-row lg:items-end lg:justify-between">
          <div className="max-w-2xl">
            <h2 className="text-2xl font-bold">AI 노출 보완 작업 큐</h2>
            <p className="mt-2 text-sm leading-6 text-blue-50/90">
              AI 언급률 측정 결과로 자동 진단된 보완 작업(AI에 더 잘 노출되도록 보완할 작업)입니다.
              우선순위 높은 항목부터 담당자·기한을 지정하고, 환자 질문에 맞춘 콘텐츠 가이드를 만들어 이번 달 운영 큐에 연결하세요.
            </p>
          </div>
          <div className="grid grid-cols-4 gap-2 text-center text-xs lg:min-w-[420px]">
            <SummaryPill label="대기" value={String(counts.open)} />
            <SummaryPill label="진행중" value={String(counts.inProgress)} />
            <SummaryPill label="확인필요" value={String(counts.blocked)} />
            <SummaryPill label="완료" value={String(counts.completed)} />
          </div>
        </div>
      </section>

      {error && (
        <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      )}

      {saveMessage && (
        <div
          className={`rounded-xl border px-4 py-3 text-sm ${
            saveMessage.kind === 'success'
              ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
              : 'border-red-200 bg-red-50 text-red-700'
          }`}
        >
          {saveMessage.text}
        </div>
      )}

      <section className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_420px]">
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <div>
              <h3 className="text-lg font-semibold text-slate-900">상위 AI 노출 보완 작업</h3>
              <p className="text-sm text-slate-500">
                현재 {actions.length}건을 우선순위 순으로 표시합니다
                {actions.length >= ACTION_LIST_LIMIT ? ` · ${ACTION_LIST_LIMIT}건까지만 표시 중` : ''}. 행을 선택하면 우측에서 상세 정보를 확인할 수 있습니다.
              </p>
            </div>
            <button
              type="button"
              onClick={loadActions}
              className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
            >
              새로고침
            </button>
          </div>

          {loading ? (
            <div className="rounded-2xl border border-slate-200 bg-white p-8 text-center text-sm text-slate-500">
              AI 노출 보완 작업을 불러오는 중입니다.
            </div>
          ) : actions.length === 0 ? (
            <div className="rounded-2xl border border-dashed border-blue-200 bg-white p-8 text-center">
              <h4 className="text-base font-semibold text-slate-900">표시할 보완 작업이 없습니다.</h4>
              <p className="mt-2 text-sm text-slate-500">
                AI 언급률 측정과 부족 진단이 끝나면 보완 작업이 자동으로 생성됩니다. 환자 질문 측정을 먼저 실행했는지 확인하세요.
              </p>
            </div>
          ) : (
            <ul className="space-y-3" role="list">
              {actions.map((action) => {
                const isSelected = action.id === selectedId
                const typeLabel = EXPOSURE_ACTION_TYPE_LABELS[action.action_type] ?? {
                  label: action.action_type,
                  color: 'bg-slate-50 text-slate-700 border-slate-200',
                }
                const statusLabel = EXPOSURE_ACTION_STATUS_LABELS[action.status] ?? {
                  label: action.status,
                  color: 'bg-slate-50 text-slate-700 border-slate-200',
                }
                const severityLabel = action.severity ? SEVERITY_LABELS[action.severity] : null

                return (
                  <li
                    key={action.id}
                    role="button"
                    tabIndex={0}
                    aria-pressed={isSelected}
                    onClick={() => setSelectedId(action.id)}
                    onKeyDown={(event) => handleRowKeyDown(event, action.id)}
                    className={`cursor-pointer rounded-2xl border bg-white p-5 shadow-sm transition focus:outline-none focus:ring-2 focus:ring-blue-200 ${
                      isSelected
                        ? 'border-blue-500 ring-2 ring-blue-100'
                        : 'border-slate-200 hover:border-slate-300'
                    }`}
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge label={typeLabel.label} color={typeLabel.color} />
                      <Badge label={statusLabel.label} color={statusLabel.color} />
                      {severityLabel && <Badge label={severityLabel.label} color={severityLabel.color} />}
                      {action.due_month && (
                        <Badge
                          label={`기한 ${action.due_month}`}
                          color="bg-indigo-50 text-indigo-700 border-indigo-200"
                        />
                      )}
                      {action.linked_content_id && (
                        <Badge
                          label="콘텐츠 연결됨"
                          color="bg-emerald-50 text-emerald-700 border-emerald-200"
                        />
                      )}
                    </div>
                    <h4 className="mt-3 text-base font-semibold text-slate-900">{action.title}</h4>
                    <p className="mt-1 text-sm text-slate-600 line-clamp-2">{action.description}</p>

                    <div className="mt-4 grid gap-3 md:grid-cols-3">
                      <InfoBlock
                        label="연결된 환자 질문"
                        value={action.query_target?.name ?? '미연결'}
                        muted={!action.query_target}
                      />
                      <InfoBlock label="담당자" value={action.owner ?? '미지정'} muted={!action.owner} />
                      <InfoBlock
                        label="진단 근거"
                        value={summarizeEvidence(action.evidence)}
                        muted={!hasEvidence(action.evidence)}
                      />
                    </div>
                  </li>
                )
              })}
            </ul>
          )}
        </div>

        <aside className="space-y-4">
          <DetailPanel
            action={selected}
            ownerDraft={ownerDraft}
            dueMonthDraft={dueMonthDraft}
            savingField={savingField}
            onOwnerDraftChange={setOwnerDraft}
            onDueMonthDraftChange={setDueMonthDraft}
            onStatusChange={(status) =>
              selected && patchAction(selected, { status }, 'status')
            }
            onOwnerCommit={() => {
              if (!selected) return
              if ((selected.owner ?? '') === ownerDraft.trim()) return
              patchAction(selected, { owner: ownerDraft.trim() || null }, 'owner')
            }}
            onDueMonthCommit={() => {
              if (!selected) return
              const trimmed = dueMonthDraft.trim()
              if ((selected.due_month ?? '') === trimmed) return
              if (trimmed && !/^\d{4}-\d{2}$/.test(trimmed)) {
                pushSaveMessage('error', '기한은 YYYY-MM 형식으로 입력해주세요.')
                return
              }
              patchAction(selected, { due_month: trimmed || null }, 'due_month')
            }}
            onCreateBrief={() => selected && handleCreateBrief(selected)}
            creatingBrief={creatingBriefId === selected?.id}
            briefResult={briefResult && selected && briefResult.actionId === selected.id ? briefResult : null}
          />
        </aside>
      </section>
    </main>
  )
}

interface DetailPanelProps {
  action: ExposureAction | null
  ownerDraft: string
  dueMonthDraft: string
  savingField: string | null
  onOwnerDraftChange: (value: string) => void
  onDueMonthDraftChange: (value: string) => void
  onStatusChange: (status: ExposureActionStatus) => void
  onOwnerCommit: () => void
  onDueMonthCommit: () => void
  onCreateBrief: () => void
  creatingBrief: boolean
  briefResult: BriefResultState | null
}

function DetailPanel({
  action,
  ownerDraft,
  dueMonthDraft,
  savingField,
  onOwnerDraftChange,
  onDueMonthDraftChange,
  onStatusChange,
  onOwnerCommit,
  onDueMonthCommit,
  onCreateBrief,
  creatingBrief,
  briefResult,
}: DetailPanelProps) {
  if (!action) {
    return (
      <div className="rounded-2xl border border-dashed border-slate-200 bg-white p-6 text-center text-sm text-slate-500">
        좌측 목록에서 보완 작업을 선택하면 상세 정보가 표시됩니다.
      </div>
    )
  }

  const statusLabel =
    EXPOSURE_ACTION_STATUS_LABELS[action.status] ?? {
      label: action.status,
      color: 'bg-slate-50 text-slate-700 border-slate-200',
    }
  const typeLabel =
    EXPOSURE_ACTION_TYPE_LABELS[action.action_type] ?? {
      label: action.action_type,
      color: 'bg-slate-50 text-slate-700 border-slate-200',
    }
  const canCreateBrief = isBriefCapableActionType(action.action_type)
  const briefGuidanceMessage = canCreateBrief
    ? '콘텐츠 가이드 생성 가능: 생성 후 콘텐츠 탭에서 운영 기준과 의료광고 리스크를 검수하세요.'
    : getBriefUnavailableMessage(action.action_type)

  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="flex flex-wrap items-center gap-2">
        <Badge label={typeLabel.label} color={typeLabel.color} />
        <Badge label={statusLabel.label} color={statusLabel.color} />
        {action.gap_type && <Badge label={formatGapType(action.gap_type)} color="bg-slate-100 text-slate-600 border-slate-200" />}
      </div>
      <h3 className="mt-3 text-lg font-semibold text-slate-900">{action.title}</h3>
      <p className="mt-2 whitespace-pre-line text-sm text-slate-600">{action.description}</p>

      <div
        className={`mt-4 rounded-xl border px-4 py-3 text-xs leading-5 ${
          canCreateBrief
            ? 'border-blue-200 bg-blue-50 text-blue-800'
            : 'border-amber-200 bg-amber-50 text-amber-800'
        }`}
      >
        <div className="font-semibold">
          {canCreateBrief ? '콘텐츠 가이드 만들기 가능' : '콘텐츠 가이드 만들기 불가'}
        </div>
        <p className="mt-1">{briefGuidanceMessage}</p>
      </div>

      <div className="mt-5 space-y-3">
        <Field label="상태">
          <select
            value={action.status}
            onChange={(event) => onStatusChange(event.target.value as ExposureActionStatus)}
            disabled={savingField === 'status'}
            className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-100 disabled:bg-slate-50"
          >
            {STATUS_OPTIONS.map((status) => (
              <option key={status} value={status}>
                {EXPOSURE_ACTION_STATUS_LABELS[status]?.label ?? status}
              </option>
            ))}
          </select>
        </Field>
        <Field label="담당자">
          <input
            value={ownerDraft}
            onChange={(event) => onOwnerDraftChange(event.target.value)}
            onBlur={onOwnerCommit}
            placeholder="담당 AE 이름"
            disabled={savingField === 'owner'}
            className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-100 disabled:bg-slate-50"
          />
        </Field>
        <Field label="기한 (월)">
          <input
            value={dueMonthDraft}
            onChange={(event) => onDueMonthDraftChange(event.target.value)}
            onBlur={onDueMonthCommit}
            placeholder="2026-06"
            disabled={savingField === 'due_month'}
            className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-100 disabled:bg-slate-50"
          />
        </Field>
      </div>

      <div className="mt-6 rounded-xl border border-slate-200 bg-slate-50 p-4">
        <h4 className="text-sm font-semibold text-slate-700">연결된 환자 질문</h4>
        {action.query_target ? (
          <div className="mt-2 space-y-1 text-sm text-slate-700">
            <div className="font-medium text-slate-900">{action.query_target.name}</div>
            <div className="text-slate-500">의도: {action.query_target.target_intent}</div>
            <div className="text-xs text-slate-500">
              우선순위 {formatQueryTargetPriority(action.query_target.priority)} · 상태 {formatQueryTargetStatus(action.query_target.status)}
              {action.query_target.target_month ? ` · ${action.query_target.target_month}` : ''}
            </div>
          </div>
        ) : (
          <p className="mt-2 text-sm text-slate-500">연결된 환자 질문이 없습니다.</p>
        )}
      </div>

      <div className="mt-4 rounded-xl border border-slate-200 bg-slate-50 p-4">
        <h4 className="text-sm font-semibold text-slate-700">진단 근거</h4>
        <EvidenceList evidence={action.evidence} />
      </div>

      <div className="mt-4 rounded-xl border border-slate-200 bg-slate-50 p-4">
        <h4 className="text-sm font-semibold text-slate-700">운영자 메타</h4>
        <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-xs text-slate-500">
          <dt>생성</dt>
          <dd className="text-slate-700">{formatDateTime(action.created_at)}</dd>
          <dt>수정</dt>
          <dd className="text-slate-700">{formatDateTime(action.updated_at)}</dd>
          <dt>완료</dt>
          <dd className="text-slate-700">{formatDateTime(action.completed_at)}</dd>
          <dt>연결 콘텐츠</dt>
          <dd className="text-slate-700">
            {formatLinkedContent(action.linked_content)}
          </dd>
        </dl>
      </div>

      <div className="mt-6 space-y-3">
        {canCreateBrief ? (
          <button
            type="button"
            onClick={onCreateBrief}
            disabled={creatingBrief}
            className="w-full rounded-xl bg-blue-600 px-4 py-3 text-sm font-semibold text-white shadow-sm hover:bg-blue-700 disabled:cursor-not-allowed disabled:bg-slate-300"
          >
            {creatingBrief ? '콘텐츠 가이드 생성 중...' : '콘텐츠 가이드 만들기'}
          </button>
        ) : (
          <button
            type="button"
            disabled
            className="w-full cursor-not-allowed rounded-xl bg-slate-300 px-4 py-3 text-sm font-semibold text-white shadow-sm"
          >
            콘텐츠 가이드 만들기 대상 아님
          </button>
        )}
        <p className="text-[11px] leading-5 text-slate-500">
          {canCreateBrief
            ? '콘텐츠 운영 기준(Essence)이 아직 승인되지 않은 경우에도 콘텐츠 가이드 초안은 생성할 수 있지만, 발행 전에 반드시 운영 기준 승인이 필요합니다. 이 화면에서는 자동 승인·발행이 일어나지 않습니다.'
            : getBriefUnavailableMessage(action.action_type)}
        </p>
        {briefResult && (
          <BriefResultPanel result={briefResult} />
        )}
      </div>
    </div>
  )
}

function BriefResultPanel({ result }: { result: BriefResultState }) {
  const { contentItem, philosophyGate } = result
  const typeLabel = TYPE_LABELS[contentItem.content_type] ?? contentItem.content_type
  return (
    <div className="rounded-xl border border-emerald-200 bg-emerald-50/60 p-4 text-sm">
      <div className="font-semibold text-emerald-800">콘텐츠 슬롯 연결 완료</div>
      <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-xs text-emerald-900/80">
        <dt>유형</dt>
        <dd>{typeLabel}</dd>
        <dt>회차</dt>
        <dd>
          {contentItem.sequence_no} / {contentItem.total_count}
        </dd>
        <dt>발행 예정</dt>
        <dd>{contentItem.scheduled_date}</dd>
        <dt>Brief 상태</dt>
        <dd>{contentItem.brief_status ?? '미정'}</dd>
        <dt>제목</dt>
        <dd className="truncate" title={contentItem.title ?? undefined}>
          {contentItem.title ?? '미작성'}
        </dd>
      </dl>
      <div
        className={`mt-3 rounded-lg border px-3 py-2 text-xs ${
          philosophyGate.has_approved_philosophy
            ? 'border-emerald-300 bg-white text-emerald-700'
            : 'border-amber-300 bg-amber-50 text-amber-800'
        }`}
      >
        {philosophyGate.has_approved_philosophy
          ? '승인된 Essence 기준이 적용되었습니다. 콘텐츠 탭에서 검수 후 발행하세요.'
          : philosophyGate.message ??
            '발행 전에 승인된 Essence(콘텐츠 운영 기준)가 필요합니다. Essence 탭에서 승인을 먼저 진행하세요.'}
      </div>
    </div>
  )
}

function EvidenceList({ evidence }: { evidence: Record<string, unknown> }) {
  const entries = Object.entries(evidence ?? {}).filter(([, value]) => !isEmptyEvidenceValue(value))
  if (entries.length === 0) {
    return <p className="mt-2 text-sm text-slate-500">기록된 근거가 없습니다.</p>
  }
  return (
    <dl className="mt-2 grid grid-cols-1 gap-x-3 gap-y-1 text-xs text-slate-600">
      {entries.map(([key, value]) => (
        <div key={key} className="flex gap-2">
          <dt className="shrink-0 font-medium text-slate-500">{formatEvidenceKey(key)}</dt>
          <dd className="text-slate-700 break-words">{formatEvidenceValue(value, key)}</dd>
        </div>
      ))}
    </dl>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="text-xs font-medium text-slate-600">{label}</span>
      <div className="mt-1">{children}</div>
    </label>
  )
}

function Badge({ label, color }: { label: string; color: string }) {
  return <span className={`inline-flex rounded-full border px-2.5 py-1 text-xs font-medium ${color}`}>{label}</span>
}

function SummaryPill({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl bg-white/10 px-4 py-3 backdrop-blur">
      <div className="text-lg font-bold text-white">{value}</div>
      <div className="mt-1 text-blue-100">{label}</div>
    </div>
  )
}

function InfoBlock({ label, value, muted }: { label: string; value: string; muted?: boolean }) {
  return (
    <div className="rounded-xl border border-slate-100 bg-slate-50 px-3 py-2">
      <div className="text-[11px] font-medium uppercase tracking-wide text-slate-400">{label}</div>
      <div className={`mt-1 truncate text-sm ${muted ? 'text-slate-400' : 'text-slate-700'}`} title={value}>
        {value}
      </div>
    </div>
  )
}

function hasEvidence(evidence: Record<string, unknown> | null | undefined): boolean {
  if (!evidence) return false
  return Object.values(evidence).some((value) => !isEmptyEvidenceValue(value))
}

function isEmptyEvidenceValue(value: unknown): boolean {
  if (value === null || value === undefined || value === '') return true
  if (value instanceof Date) return Number.isNaN(value.getTime())
  if (Array.isArray(value)) return value.length === 0
  if (typeof value === 'object') return Object.keys(value as Record<string, unknown>).length === 0
  return false
}

function isBriefCapableActionType(actionType: ExposureAction['action_type']): boolean {
  return BRIEF_CAPABLE_ACTION_TYPES.has(actionType as ExposureActionType)
}

function getBriefUnavailableMessage(actionType: ExposureAction['action_type']): string {
  if (actionType === 'MEASUREMENT') {
    return '측정 작업은 콘텐츠 가이드 생성 대상이 아닙니다. 활성 질문을 확인한 뒤 첫 AI 답변 언급률 측정을 실행해 처리하세요.'
  }
  return '이 작업 유형은 콘텐츠 가이드 생성 대상이 아닙니다. 작업 설명에 따라 큐에서 처리하세요.'
}

function formatGapType(gapType: string): string {
  return GAP_TYPE_LABELS[gapType] ?? gapType.replaceAll('_', ' ').toLowerCase()
}

function formatQueryTargetPriority(priority: string): string {
  return QUERY_TARGET_PRIORITY_LABELS[priority as keyof typeof QUERY_TARGET_PRIORITY_LABELS]?.label ?? priority
}

function formatQueryTargetStatus(status: string): string {
  return QUERY_TARGET_STATUS_LABELS[status as keyof typeof QUERY_TARGET_STATUS_LABELS]?.label ?? status
}

function formatLinkedContent(content: ExposureActionContentSummary | null | undefined): string {
  if (!content) return '미연결'
  const typeLabel = TYPE_LABELS[content.content_type] ?? content.content_type
  const title = content.title ?? '제목 미작성'
  return `${typeLabel} ${content.sequence_no}/${content.total_count} · ${content.scheduled_date} · ${title}`
}

function summarizeEvidence(evidence: Record<string, unknown> | null | undefined): string {
  if (!evidence) return '근거 없음'
  const entries = Object.entries(evidence).filter(([, value]) => !isEmptyEvidenceValue(value))
  if (entries.length === 0) return '근거 없음'
  return entries
    .slice(0, 2)
    .map(([key, value]) => `${formatEvidenceKey(key)}: ${formatEvidenceValue(value, key)}`)
    .join(' · ')
}

function formatEvidenceKey(key: string): string {
  const direct = EVIDENCE_KEY_LABELS[key] ?? EVIDENCE_KEY_LABELS[key.toLowerCase()]
  if (direct) return direct
  return key.replaceAll('_', ' ')
}

function formatEvidenceValueLabel(value: string): string {
  return EVIDENCE_VALUE_LABELS[value] ?? EVIDENCE_VALUE_LABELS[value.toLowerCase()] ?? value
}

function formatNumberForKey(value: number, key?: string): string {
  if (key && PERCENT_KEY_RE.test(key)) {
    const pct = value > 0 && value <= 1 ? value * 100 : value
    const rounded = Math.round(pct * 10) / 10
    return `${rounded}%`
  }
  if (Number.isInteger(value)) return String(value)
  return String(Math.round(value * 100) / 100)
}

function formatEvidenceValue(value: unknown, key?: string): string {
  if (value === null || value === undefined || value === '') return '-'
  if (typeof value === 'boolean') return value ? '예' : '아니오'
  if (typeof value === 'number') return formatNumberForKey(value, key)
  if (value instanceof Date) return Number.isNaN(value.getTime()) ? '-' : formatDateTime(value.toISOString())
  if (typeof value === 'string') {
    if (/^\d{4}-\d{2}-\d{2}T/.test(value)) return formatDateTime(value)
    return formatEvidenceValueLabel(value)
  }
  if (Array.isArray(value)) {
    if (value.length === 0) return '-'
    const items = value
      .slice(0, 5)
      .map((item) =>
        typeof item === 'string' || typeof item === 'number' || typeof item === 'boolean'
          ? formatEvidenceValue(item, key)
          : formatEvidenceValue(item),
      )
    const more = value.length > items.length ? ` 외 ${value.length - items.length}건` : ''
    return `${items.join(', ')}${more}`
  }
  if (typeof value === 'object') {
    const entries = Object.entries(value as Record<string, unknown>).filter(
      ([, v]) => !isEmptyEvidenceValue(v),
    )
    if (entries.length === 0) return '-'
    return entries
      .slice(0, 4)
      .map(([k, v]) => `${formatEvidenceKey(k)}: ${formatEvidenceValue(v, k)}`)
      .join(', ')
  }
  return String(value)
}

function formatDateTime(value: string | null | undefined): string {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('ko-KR', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}
