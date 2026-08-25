'use client'

import { KeyboardEvent, useCallback, useEffect, useMemo, useState } from 'react'
import { useParams } from 'next/navigation'
import { fetchAPI } from '@/lib/api'
import {
  EXPOSURE_ACTION_LIST_LIMIT,
  summarizeExposureActions,
} from '@/lib/exposure-action-counts'
import { groupExposureActions, type ExposureActionGroup } from '@/lib/exposure-action-groups'
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
  TARGET_NOT_MEASURED: '아직 측정 안 된 질문',
  MISSING_MENTION: '병원 미언급',
  LOW_MENTION_RATE: '낮은 AI 언급률',
  MENTIONS_COMPETITOR_ONLY: '경쟁 병원만 언급',
  COMPETITOR_VISIBILITY: '경쟁 병원이 더 많이 노출',
  COMPETITOR_DOMINANCE: '경쟁 병원이 더 많이 노출',
  NO_PUBLIC_CONTENT: '대응 콘텐츠 없음',
  WEAK_ENTITY_FACTS: '병원 기본 정보 부족',
  TECHNICAL_CRAWL_GAP: '검색 반영 보강',
  SOURCE_GAP: 'AI가 참고할 근거 자료 부족',
  SOURCE_SIGNAL_GAP: 'AI가 참고할 근거 자료 부족',
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
  total_measurements: '전체 측정 수',
  successful_measurements: '성공 측정 수',
  failed_measurements: '실패 측정 수',
  hospital_successful_measurements: '병원 전체 성공 측정 수',
  source_missing_count: '근거 URL 부족 수',
  competitor_mention_count: '경쟁 병원 언급 수',
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
  latest_measured_at: '최근 측정',
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
  target_not_measured_yet: '이 질문 아직 미측정',
  missing_mention: '병원 미언급',
  competitor_visibility: '경쟁 병원이 더 많이 노출',
  source_signal_gap: 'AI가 참고할 근거 자료 부족',
  zero_hospital_mentions: '병원 미언급',
  mention_rate_below_threshold: 'AI 언급률 기준 미달',
  competitor_mentions_match_or_exceed_hospital_mentions: '경쟁 병원 언급 우세',
  source_urls_missing_for_majority_of_successful_measurements: '참고 URL 부족',
  HIGH: '높음',
  NORMAL: '보통',
  LOW: '낮음',
  high: '높음',
  normal: '보통',
  low: '낮음',
}

const PERCENT_KEY_RE = /(rate|share_of_voice|sov|percent|pct)/i

interface BriefResultState {
  actionId: string
  contentItem: ExposureActionContentSummary
  philosophyGate: ExposureActionCreateBriefResponse['philosophy_gate']
}

interface AdminAccount {
  id: string
  email: string
  name: string
  is_active: boolean
}

export default function ExposureActionsPage() {
  const params = useParams<{ id: string }>()
  const hospitalId = params.id

  const [actions, setActions] = useState<ExposureAction[]>([])
  const [accounts, setAccounts] = useState<AdminAccount[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)

  const [savingField, setSavingField] = useState<string | null>(null)
  const [saveMessage, setSaveMessage] = useState<{ kind: 'success' | 'error'; text: string } | null>(null)

  const [creatingBriefId, setCreatingBriefId] = useState<string | null>(null)
  const [briefResult, setBriefResult] = useState<BriefResultState | null>(null)
  const [refreshing, setRefreshing] = useState(false)

  const [ownerDraft, setOwnerDraft] = useState('')
  const [dueMonthDraft, setDueMonthDraft] = useState('')

  const groups = useMemo(() => groupExposureActions(actions), [actions])
  const selectedGroup = useMemo(
    () => groups.find((group) => group.actions.some((action) => action.id === selectedId)) ?? null,
    [groups, selectedId],
  )
  const selected = selectedGroup?.representative ?? null

  useEffect(() => {
    if (selected) {
      const owner = selectedGroup?.commonOwner
      setOwnerDraft(owner ? (accounts.find((account) => account.email === owner)?.id ?? '') : '')
      setDueMonthDraft(selectedGroup?.commonDueMonth ?? '')
    } else {
      setOwnerDraft('')
      setDueMonthDraft('')
    }
  }, [accounts, selected, selectedGroup])

  useEffect(() => {
    void fetchAPI<AdminAccount[]>('/admin/accounts')
      .then((rows) => setAccounts(rows.filter((account) => account.is_active)))
      .catch(() => setAccounts([]))
  }, [])

  const loadActions = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data: ExposureAction[] = await fetchAPI(
        `/admin/hospitals/${hospitalId}/exposure-actions?limit=${EXPOSURE_ACTION_LIST_LIMIT}`,
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

  // 진단은 측정 직후 워커에서만 갱신된다. 측정 결과가 바뀐 뒤 큐가 옛 진단을 계속
  // 보여주면 같은 화면의 언급률과 어긋나므로, 운영자가 직접 다시 진단할 수 있게 한다.
  const refreshDiagnosis = useCallback(async () => {
    setRefreshing(true)
    setError(null)
    try {
      const data: ExposureAction[] = await fetchAPI(
        `/admin/hospitals/${hospitalId}/exposure-actions/refresh?limit=${EXPOSURE_ACTION_LIST_LIMIT}`,
        { method: 'POST' },
      )
      const next = data ?? []
      setActions(next)
      setSelectedId((prev) => {
        if (prev && next.some((action) => action.id === prev)) return prev
        return next[0]?.id ?? null
      })
    } catch (err) {
      setError(err instanceof Error ? err.message : '진단을 다시 실행하지 못했습니다.')
    } finally {
      setRefreshing(false)
    }
  }, [hospitalId])

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

  async function patchGroup(group: ExposureActionGroup, patch: Record<string, unknown>, fieldKey: string) {
    setSavingField(fieldKey)
    setSaveMessage(null)
    try {
      const updated = await fetchAPI<ExposureAction[]>(
        `/admin/hospitals/${hospitalId}/exposure-actions/group`,
        {
          method: 'PATCH',
          body: JSON.stringify({ action_ids: group.actions.map((action) => action.id), ...patch }),
        },
      )
      const byId = new Map(updated.map((action) => [action.id, action]))
      setActions((previous) => previous.map((action) => byId.get(action.id) ?? action))
      pushSaveMessage('success', `${group.questionCount}개 질문 작업에 함께 저장했습니다.`)
    } catch (err) {
      pushSaveMessage('error', err instanceof Error ? err.message : '묶음 작업을 저장하지 못했습니다.')
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

  const counts = useMemo(
    () => summarizeExposureActions(groups.map((group) => group.representative)),
    [groups],
  )

  return (
    <main className="min-h-full space-y-6 bg-slate-50 p-4 sm:p-6 lg:p-8">
      <section className="rounded-2xl bg-slate-900 p-5 text-white sm:p-7">
        <p className="text-[11px] font-semibold uppercase tracking-[0.2em] text-blue-200">
          AI 노출 운영 작업 큐
        </p>
        <div className="mt-2 flex flex-col gap-5 lg:flex-row lg:items-end lg:justify-between">
          <div className="max-w-2xl">
            <h2 className="text-2xl font-bold">AI 노출 보완 작업 큐</h2>
            <p className="mt-2 text-sm leading-6 text-blue-50/90">
              AI 언급률 측정 결과로 자동 진단된 보완 작업(AI에 더 잘 노출되도록 보완할 작업)입니다.
              우선순위 높은 항목부터 담당자·기한을 지정하고, 환자 질문에 맞춘 콘텐츠 가이드를 만들어 이번 달 운영 큐에 연결하세요.
            </p>
          </div>
          <div className="w-full lg:min-w-[420px] lg:w-auto">
            {/*
              완료 항목은 이 조회가 애초에 돌려주지 않는다(백엔드는 대기·진행중·확인필요만
              반환한다). 항상 0인 칸을 "완료"라고 세우면 완료된 작업이 없다는 거짓 신호가
              된다 — 남은 작업 합계로 바꿨다(A-6).
            */}
            <div className="grid grid-cols-2 gap-2 text-center text-xs sm:grid-cols-4">
              <SummaryPill label="대기" value={String(counts.waiting)} />
              <SummaryPill label="진행중" value={String(counts.inProgress)} />
              <SummaryPill label="확인필요" value={String(counts.blocked)} />
              <SummaryPill label="남은 작업" value={String(counts.active)} />
            </div>
            <button
              type="button"
              onClick={refreshDiagnosis}
              disabled={refreshing || loading}
              className="mt-2 w-full rounded-lg border border-white/25 px-3 py-2 text-xs font-semibold text-white hover:bg-white/10 disabled:opacity-50"
            >
              {refreshing ? '진단 다시 실행 중...' : '최신 측정으로 진단 다시 실행'}
            </button>
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
                진단 유형 {groups.length}건 · 연결 질문 {actions.length}개를 우선순위 순으로 표시합니다
                {actions.length >= EXPOSURE_ACTION_LIST_LIMIT ? ` · ${EXPOSURE_ACTION_LIST_LIMIT}건까지만 표시 중` : ''}. 행을 선택하면 우측에서 상세 정보를 확인할 수 있습니다.
              </p>
            </div>
            <button
              type="button"
              onClick={loadActions}
              className="min-h-11 min-w-fit shrink-0 whitespace-nowrap rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
            >
              새로고침
            </button>
          </div>

          {loading ? (
            <div className="rounded-2xl border border-slate-200 bg-white p-8 text-center text-sm text-slate-500">
              AI 노출 보완 작업을 불러오는 중입니다.
            </div>
          ) : groups.length === 0 ? (
            <div className="rounded-2xl border border-dashed border-blue-200 bg-white p-8 text-center">
              <h4 className="text-base font-semibold text-slate-900">표시할 보완 작업이 없습니다.</h4>
              <p className="mt-2 text-sm text-slate-500">
                AI 언급률 측정과 부족 진단이 끝나면 보완 작업이 자동으로 생성됩니다. 환자 질문 측정을 먼저 실행했는지 확인하세요.
              </p>
            </div>
          ) : (
            <ul className="space-y-3" role="list">
              {groups.map((group) => {
                const action = group.representative
                const isSelected = group.actions.some((member) => member.id === selectedId)
                const typeLabel = EXPOSURE_ACTION_TYPE_LABELS[action.action_type] ?? {
                  label: action.display?.action_type_label ?? action.action_type,
                  color: 'bg-slate-50 text-slate-700 border-slate-200',
                }
                const statusLabel = EXPOSURE_ACTION_STATUS_LABELS[action.status] ?? {
                  label: action.display?.status_label ?? action.status,
                  color: 'bg-slate-50 text-slate-700 border-slate-200',
                }
                const severityLabel = action.severity ? getSeverityLabel(action) : null

                return (
                  <li
                    key={group.key}
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
                    <h4 className="mt-3 text-base font-semibold text-slate-900">
                      {action.title} (연결 질문 {group.questionCount}개)
                    </h4>
                    <p className="mt-1 text-sm text-slate-600 line-clamp-2">{action.description}</p>

                    <div className="mt-4 grid gap-3 md:grid-cols-3">
                      <InfoBlock
                        label="연결된 환자 질문"
                        value={`${group.questionCount}개 질문 체크리스트`}
                      />
                      <InfoBlock label="담당자" value={group.commonOwner ?? (group.actions.some((member) => member.owner) ? '담당자 혼합' : '미지정')} muted={!group.commonOwner} />
                      <InfoBlock
                        label="진단 근거"
                        value={summarizeEvidence(action)}
                        muted={!hasEvidence(action)}
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
            group={selectedGroup}
            accounts={accounts}
            ownerDraft={ownerDraft}
            dueMonthDraft={dueMonthDraft}
            savingField={savingField}
            onDueMonthDraftChange={setDueMonthDraft}
            onStatusChange={(status) =>
              selectedGroup && patchGroup(selectedGroup, { status }, 'status')
            }
            onOwnerChange={(owner) => {
              setOwnerDraft(owner)
              if (!selectedGroup) return
              const account = accounts.find((candidate) => candidate.id === owner)
              if (selectedGroup.commonOwner === (account?.email ?? null)) return
              void patchGroup(selectedGroup, { owner_account_id: owner || null }, 'owner')
            }}
            onDueMonthCommit={() => {
              if (!selectedGroup) return
              const trimmed = dueMonthDraft.trim()
              if ((selectedGroup.commonDueMonth ?? '') === trimmed) return
              if (trimmed && !/^\d{4}-\d{2}$/.test(trimmed)) {
                pushSaveMessage('error', '기한은 YYYY-MM 형식으로 입력해주세요.')
                return
              }
              patchGroup(selectedGroup, { due_month: trimmed || null }, 'due_month')
            }}
            onQuestionToggle={(action, completed) => void patchAction(action, { status: completed ? 'COMPLETED' : 'OPEN' }, `question:${action.id}`)}
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
  group: ExposureActionGroup | null
  accounts: readonly AdminAccount[]
  ownerDraft: string
  dueMonthDraft: string
  savingField: string | null
  onDueMonthDraftChange: (value: string) => void
  onStatusChange: (status: ExposureActionStatus) => void
  onOwnerChange: (value: string) => void
  onDueMonthCommit: () => void
  onQuestionToggle: (action: ExposureAction, completed: boolean) => void
  onCreateBrief: () => void
  creatingBrief: boolean
  briefResult: BriefResultState | null
}

function DetailPanel({
  action,
  group,
  accounts,
  ownerDraft,
  dueMonthDraft,
  savingField,
  onDueMonthDraftChange,
  onStatusChange,
  onOwnerChange,
  onDueMonthCommit,
  onQuestionToggle,
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
      label: action.display?.status_label ?? action.status,
      color: 'bg-slate-50 text-slate-700 border-slate-200',
    }
  const typeLabel =
    EXPOSURE_ACTION_TYPE_LABELS[action.action_type] ?? {
      label: action.display?.action_type_label ?? action.action_type,
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
        {action.gap_type && <Badge label={formatGapType(action)} color="bg-slate-100 text-slate-600 border-slate-200" />}
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
        <Field label="담당자 계정">
          <select
            value={ownerDraft}
            onChange={(event) => onOwnerChange(event.target.value)}
            disabled={savingField === 'owner'}
            className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-100 disabled:bg-slate-50"
          >
            <option value="">미지정</option>
            {accounts.map((account) => (
              <option key={account.id} value={account.id}>{account.name} · {account.email}</option>
            ))}
          </select>
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
        <h4 className="text-sm font-semibold text-slate-700">연결 질문 체크리스트 · {group?.questionCount ?? 0}개</h4>
        {group && group.actions.length > 0 ? (
          <ul className="mt-2 space-y-2">
            {group.actions.map((member) => (
              <li key={member.id} className="rounded-lg border border-slate-200 bg-white px-3 py-2">
                <label className="flex items-start gap-2 text-sm text-slate-800">
                  <input type="checkbox" checked={member.status === 'COMPLETED'} disabled={savingField === `question:${member.id}`} onChange={(event) => onQuestionToggle(member, event.target.checked)} className="mt-0.5 rounded border-slate-300" />
                  <span>
                    <span className="font-medium">{member.query_target?.name ?? '연결 질문 없음'}</span>
                    {member.query_target ? <span className="mt-0.5 block text-xs text-slate-500">의도 {member.query_target.target_intent} · 우선순위 {formatQueryTargetPriority(member.query_target.priority)} · {formatQueryTargetStatus(member.query_target.status)}</span> : null}
                  </span>
                </label>
              </li>
            ))}
          </ul>
        ) : <p className="mt-2 text-sm text-slate-500">연결된 환자 질문이 없습니다.</p>}
      </div>

      <div className="mt-4 rounded-xl border border-slate-200 bg-slate-50 p-4">
        <h4 className="text-sm font-semibold text-slate-700">진단 근거</h4>
        <EvidenceList action={action} />
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
            ? '콘텐츠 운영 기준이 아직 자동 승인되지 않아도 콘텐츠 가이드 초안은 만들 수 있습니다. 시스템 검수가 승인하면 예약일에 자동 발행되며, 보류된 예외만 운영 기준 탭에서 확인합니다.'
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
        <dt>콘텐츠 가이드 상태</dt>
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
          ? '승인된 콘텐츠 운영 기준이 적용되었습니다. 예약일 자동 발행 후 콘텐츠 탭에서 공개 내용을 확인하세요.'
          : philosophyGate.message ??
            '시스템 자동 승인이 아직 완료되지 않았습니다. 운영 기준 탭에서 자동 검수 상태와 보류 사유를 확인하세요.'}
      </div>
    </div>
  )
}

function EvidenceList({ action }: { action: ExposureAction }) {
  const displayItems = action.display?.evidence_items?.filter((item) => item.label && item.value) ?? []
  const fallbackEntries = Object.entries(action.evidence ?? {}).filter(([, value]) => !isEmptyEvidenceValue(value))
  if (displayItems.length === 0 && fallbackEntries.length === 0) {
    return <p className="mt-2 text-sm text-slate-500">기록된 근거가 없습니다.</p>
  }
  return (
    <dl className="mt-2 grid grid-cols-1 gap-x-3 gap-y-1 text-xs text-slate-600">
      {displayItems.length > 0
        ? displayItems.map((item) => (
            <div key={item.key} className="flex gap-2">
              <dt className="shrink-0 font-medium text-slate-500">{item.label}</dt>
              <dd className="text-slate-700 break-words">{item.value}</dd>
            </div>
          ))
        : fallbackEntries.map(([key, value]) => (
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

function hasEvidence(action: ExposureAction): boolean {
  if (action.display?.evidence_items && action.display.evidence_items.length > 0) return true
  const evidence = action.evidence
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

function getSeverityLabel(action: ExposureAction): { label: string; color: string } {
  return SEVERITY_LABELS[action.severity ?? ''] ?? {
    label: action.display?.severity_label ?? action.severity ?? '',
    color: 'bg-slate-50 text-slate-600 border-slate-200',
  }
}

function formatGapType(action: ExposureAction): string {
  if (action.display?.gap_type_label) return action.display.gap_type_label
  const gapType = action.gap_type
  if (!gapType) return ''
  return GAP_TYPE_LABELS[gapType] ?? gapType.replaceAll('_', ' ').toLowerCase()
}

function formatQueryTargetPriority(priority: string): string {
  return QUERY_TARGET_PRIORITY_LABELS[priority as keyof typeof QUERY_TARGET_PRIORITY_LABELS]?.label ?? priority
}

function formatQueryTargetStatus(status: string): string {
  return QUERY_TARGET_STATUS_LABELS[status as keyof typeof QUERY_TARGET_STATUS_LABELS]?.label ?? '상태 확인 필요'
}

function formatLinkedContent(content: ExposureActionContentSummary | null | undefined): string {
  if (!content) return '미연결'
  const typeLabel = TYPE_LABELS[content.content_type] ?? content.content_type
  const title = content.title ?? '제목 미작성'
  return `${typeLabel} ${content.sequence_no}/${content.total_count} · ${content.scheduled_date} · ${title}`
}

function summarizeEvidence(action: ExposureAction): string {
  if (action.display?.evidence_summary) return action.display.evidence_summary
  const evidence = action.evidence
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
