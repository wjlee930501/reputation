'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
import { ApiError, fetchAPI } from '@/lib/api'
import { ADMIN_COPY } from '@/lib/admin-copy'
import { isExpectedOperatorRequestFailure } from '@/lib/operations-journey'
import {
  SHARE_LABELS,
  addMonths,
  draftChanged,
  draftFromState,
  focusSummary,
  matchLabel,
  monthProgress,
  monthShortfall,
  normalizeTopic,
  periodLabel,
  periodProblem,
  topicInputProblem,
  type ContentFocusDraft,
  type ContentFocusState,
  type ContentFocusTopicMatch,
} from '@/lib/content-focus'

const TONE_CLASSES = {
  good: 'border-emerald-200 bg-emerald-50 text-emerald-900',
  warn: 'border-amber-300 bg-amber-50 text-amber-900',
  pending: 'border-slate-200 bg-slate-50 text-slate-600',
} as const

function currentMonth(): string {
  // 한국 시간 기준 이번 달 — 브라우저 시간대와 무관하게 맞춘다.
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Seoul', year: 'numeric', month: '2-digit' })
    .format(new Date())
    .slice(0, 7)
}

/** 콘텐츠 화면의 '콘텐츠 방향' 카드 — 원장님이 원하는 진료 항목으로 다음 글의 주제를 기운다. */
export function ContentFocusSection({ hospitalId }: { hospitalId: string }) {
  const [state, setState] = useState<ContentFocusState | null>(null)
  const [draft, setDraft] = useState<ContentFocusDraft>(draftFromState(null))
  const [matches, setMatches] = useState<ContentFocusTopicMatch[]>([])
  const [topicInput, setTopicInput] = useState('')
  const [inputProblem, setInputProblem] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [saved, setSaved] = useState<string | null>(null)
  const [open, setOpen] = useState(false)
  const previewSeq = useRef(0)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    fetchAPI<ContentFocusState>(`/admin/hospitals/${hospitalId}/content-focus`)
      .then((value) => {
        if (cancelled || !value) return
        setState(value)
        setDraft(draftFromState(value))
        setMatches(value.topic_matches)
        // 켜져 있으면 처음부터 펼쳐 지금 방향이 보이게 한다.
        setOpen(value.topics.length > 0)
      })
      .catch((e: unknown) => {
        if (cancelled) return
        if (!isExpectedOperatorRequestFailure(e)) throw e
        setLoadError(ADMIN_COPY.contentFocus.loadFailed)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => { cancelled = true }
  }, [hospitalId])

  // 주제를 바꾸면 맞는 환자 질문 수를 다시 센다(저장하지 않는다).
  const topicsKey = draft.topics.join('\u0001')
  useEffect(() => {
    // 주제가 바뀔 때마다 앞선 미리보기 응답을 무효로 한다(캐시로 끝나는 경우도).
    const seq = ++previewSeq.current
    if (loading || loadError) return
    const known = new Set(matches.map((match) => match.topic))
    if (draft.topics.every((topic) => known.has(topic))) return
    const timer = setTimeout(() => {
      fetchAPI<{ topic_matches: ContentFocusTopicMatch[] }>(
        `/admin/hospitals/${hospitalId}/content-focus/preview`,
        { method: 'POST', body: JSON.stringify({ topics: draft.topics }) },
      )
        .then((value) => {
          if (seq !== previewSeq.current || !value) return
          // 이미 아는 주제의 결과는 지우지 않고 합친다.
          setMatches((current) => {
            const merged = new Map(current.map((match) => [match.topic, match]))
            for (const match of value.topic_matches) merged.set(match.topic, match)
            return [...merged.values()]
          })
        })
        .catch((e: unknown) => {
          if (!isExpectedOperatorRequestFailure(e)) throw e
        })
    }, 250)
    return () => clearTimeout(timer)
    // matches는 일부러 빼었다 — 미리보기 결과가 다시 미리보기를 부르지 않게.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [topicsKey, hospitalId, loading, loadError])

  const matchByTopic = useMemo(() => new Map(matches.map((match) => [match.topic, match])), [matches])
  const maxTopics = state?.max_topics ?? 5
  const shareChoices = state?.share_choices ?? [30, 50, 70, 100]
  const thisMonth = currentMonth()
  const changed = draftChanged(state, draft)
  const period = periodProblem(draft.fromMonth, draft.untilMonth)
  const enabled = (state?.topics.length ?? 0) > 0

  function addTopic() {
    const problem = topicInputProblem(topicInput, draft.topics, maxTopics)
    const topic = normalizeTopic(topicInput)
    if (problem) {
      setInputProblem(problem)
      return
    }
    if (!topic) return
    setDraft((current) => ({ ...current, topics: [...current.topics, topic] }))
    setTopicInput('')
    setInputProblem(null)
    setSaved(null)
  }

  function removeTopic(topic: string) {
    setDraft((current) => ({ ...current, topics: current.topics.filter((value) => value !== topic) }))
    setSaved(null)
  }

  async function save(next: ContentFocusDraft) {
    setSaving(true)
    setSaveError(null)
    setSaved(null)
    try {
      const value = await fetchAPI<ContentFocusState>(`/admin/hospitals/${hospitalId}/content-focus`, {
        method: 'PATCH',
        body: JSON.stringify({
          topics: next.topics,
          share: next.share,
          from_month: next.fromMonth || null,
          until_month: next.untilMonth || null,
        }),
      })
      if (value) {
        setState(value)
        setDraft(draftFromState(value))
        setMatches(value.topic_matches)
        setSaved(value.topics.length > 0 ? ADMIN_COPY.contentFocus.savedOn : ADMIN_COPY.contentFocus.savedOff)
      }
    } catch (e: unknown) {
      if (!isExpectedOperatorRequestFailure(e)) throw e
      setSaveError(e instanceof ApiError ? e.message : ADMIN_COPY.contentFocus.saveFailed)
    } finally {
      setSaving(false)
    }
  }

  return (
    <details
      id="content-focus"
      open={open}
      onToggle={(e) => setOpen(e.currentTarget.open)}
      className="mt-4 rounded-xl border border-slate-200 bg-white"
    >
      <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between gap-3 px-4 py-3 text-sm font-semibold text-slate-800 [&::-webkit-details-marker]:hidden">
        <span>
          {ADMIN_COPY.contentFocus.title} · {loading ? '확인 중' : focusSummary(state)}
        </span>
        <span aria-hidden className="text-xs text-slate-400">{open ? '접기' : '펼치기'}</span>
      </summary>

      <div className="space-y-5 border-t border-slate-100 p-4 sm:p-5">
        <p className="text-sm leading-6 text-slate-600">{ADMIN_COPY.contentFocus.intro}</p>

        {loadError && (
          <p className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">{loadError}</p>
        )}

        {!loading && !loadError && (
          // 저장하는 동안은 입력을 막는다 — 응답이 그 사이 고친 값을 덮지 않게.
          <fieldset disabled={saving} className="min-w-0 space-y-5 disabled:opacity-70">
            {/* 1. 주력 주제 */}
            <section>
              <h3 className="text-sm font-semibold text-slate-800">1. {ADMIN_COPY.contentFocus.topicsHeading}</h3>
              <p className="mt-1 text-xs text-slate-500">{ADMIN_COPY.contentFocus.topicsHint}</p>
              <div className="mt-2 flex flex-col gap-2 sm:flex-row">
                <input
                  type="text"
                  value={topicInput}
                  maxLength={30}
                  onChange={(e) => {
                    setTopicInput(e.target.value)
                    setInputProblem(null)
                  }}
                  onKeyDown={(e) => {
                    // 한글 조합을 끝내는 Enter(Safari는 keyCode 229)는 주제를 더하지 않는다.
                    if (e.key === 'Enter' && !e.nativeEvent.isComposing && e.keyCode !== 229) {
                      e.preventDefault()
                      addTopic()
                    }
                  }}
                  placeholder={ADMIN_COPY.contentFocus.topicPlaceholder}
                  aria-label={ADMIN_COPY.contentFocus.topicsHeading}
                  className="min-h-11 flex-1 rounded-lg border border-slate-300 px-3 text-sm focus:border-blue-500 focus:outline-none"
                />
                <button
                  type="button"
                  onClick={addTopic}
                  disabled={!normalizeTopic(topicInput) || draft.topics.length >= maxTopics}
                  className="min-h-11 rounded-lg border border-slate-300 px-4 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  추가
                </button>
              </div>
              {inputProblem && <p className="mt-1 text-xs text-red-700">{inputProblem}</p>}

              {draft.topics.length === 0 ? (
                <p className="mt-3 text-sm text-slate-500">{ADMIN_COPY.contentFocus.noTopics}</p>
              ) : (
                <ul className="mt-3 space-y-2">
                  {draft.topics.map((topic) => {
                    const match = matchByTopic.get(topic)
                    const label = matchLabel(match)
                    return (
                      <li key={topic} className={`rounded-lg border px-3 py-2 ${TONE_CLASSES[label.tone]}`}>
                        <div className="flex items-center justify-between gap-3">
                          <div className="min-w-0">
                            <p className="text-sm font-semibold">{topic}</p>
                            <p className="text-xs">{label.text}</p>
                          </div>
                          <button
                            type="button"
                            onClick={() => removeTopic(topic)}
                            className="shrink-0 rounded-md px-2 py-1 text-xs font-medium hover:bg-white/70"
                            aria-label={`${topic} 빼기`}
                          >
                            빼기
                          </button>
                        </div>
                        {match && match.examples.length > 0 && (
                          <p className="mt-1 truncate text-xs opacity-80" title={match.examples.join('\n')}>
                            예: {match.examples.join(' · ')}
                          </p>
                        )}
                      </li>
                    )
                  })}
                </ul>
              )}

              {draft.topics.length === 0 && (state?.legacy_topics.length ?? 0) > 0 && (
                <p className="mt-2 text-xs text-slate-500">
                  {ADMIN_COPY.contentFocus.legacyNote} {state?.legacy_topics.join(', ')}
                </p>
              )}
            </section>

            {/* 2. 기간 */}
            <section>
              <h3 className="text-sm font-semibold text-slate-800">2. {ADMIN_COPY.contentFocus.periodHeading}</h3>
              <p className="mt-1 text-xs text-slate-500">{ADMIN_COPY.contentFocus.periodHint}</p>
              <div className="mt-2 grid gap-2 sm:grid-cols-2">
                <label className="text-xs text-slate-600">
                  시작 달
                  <input
                    type="month"
                    value={draft.fromMonth}
                    onChange={(e) => setDraft((current) => ({ ...current, fromMonth: e.target.value }))}
                    className="mt-1 block min-h-11 w-full rounded-lg border border-slate-300 px-3 text-sm"
                  />
                </label>
                <label className="text-xs text-slate-600">
                  끝 달
                  <input
                    type="month"
                    value={draft.untilMonth}
                    min={draft.fromMonth || undefined}
                    onChange={(e) => setDraft((current) => ({ ...current, untilMonth: e.target.value }))}
                    className="mt-1 block min-h-11 w-full rounded-lg border border-slate-300 px-3 text-sm"
                  />
                </label>
              </div>
              <div className="mt-2 flex flex-wrap gap-2">
                {[1, 2, 3].map((months) => (
                  <button
                    key={months}
                    type="button"
                    onClick={() =>
                      setDraft((current) => ({
                        ...current,
                        fromMonth: thisMonth,
                        untilMonth: addMonths(thisMonth, months - 1),
                      }))
                    }
                    className="rounded-full border border-slate-300 px-3 py-1 text-xs text-slate-700 hover:bg-slate-50"
                  >
                    이번 달부터 {months}개월
                  </button>
                ))}
                <button
                  type="button"
                  onClick={() => setDraft((current) => ({ ...current, fromMonth: '', untilMonth: '' }))}
                  className="rounded-full border border-slate-300 px-3 py-1 text-xs text-slate-700 hover:bg-slate-50"
                >
                  기간 없이 계속
                </button>
              </div>
              <p className="mt-2 text-xs font-medium text-slate-700">
                {period ?? `적용 기간: ${periodLabel(draft.fromMonth || null, draft.untilMonth || null)}`}
              </p>
            </section>

            {/* 3. 비중 */}
            <section>
              <h3 className="text-sm font-semibold text-slate-800">3. {ADMIN_COPY.contentFocus.shareHeading}</h3>
              <p className="mt-1 text-xs text-slate-500">{ADMIN_COPY.contentFocus.shareHint}</p>
              <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-4" role="radiogroup" aria-label={ADMIN_COPY.contentFocus.shareHeading}>
                {shareChoices.map((share) => {
                  const selected = draft.share === share
                  return (
                    <button
                      key={share}
                      type="button"
                      role="radio"
                      aria-checked={selected}
                      onClick={() => setDraft((current) => ({ ...current, share }))}
                      className={`min-h-11 rounded-lg border px-3 text-sm font-medium ${
                        selected
                          ? 'border-blue-600 bg-blue-600 text-white'
                          : 'border-slate-300 text-slate-700 hover:bg-slate-50'
                      }`}
                    >
                      {SHARE_LABELS[share] ?? ''} {share}%
                    </button>
                  )
                })}
              </div>
            </section>

            {/* 지금 상태 */}
            {enabled && state && state.months.some((month) => month.active) && (
              <section className="rounded-lg border border-blue-200 bg-blue-50 px-3 py-2">
                <h3 className="text-xs font-semibold text-blue-900">{ADMIN_COPY.contentFocus.progressHeading}</h3>
                <ul className="mt-1 space-y-1 text-xs text-blue-900">
                  {state.months.map((month) => {
                    const progress = monthProgress(month)
                    if (!progress) return null
                    const shortfall = monthShortfall(month)
                    return (
                      <li key={month.month}>
                        {progress}
                        {shortfall && <span className="block text-amber-800">{shortfall}</span>}
                      </li>
                    )
                  })}
                </ul>
              </section>
            )}

            <ul className="list-disc space-y-1 pl-5 text-xs text-slate-500">
              {ADMIN_COPY.contentFocus.notes.map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>

            {saveError && (
              <p className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">{saveError}</p>
            )}
            {saved && (
              <p className="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-900">{saved}</p>
            )}

            <div className="flex flex-col gap-2 sm:flex-row sm:justify-end">
              {enabled && (
                <button
                  type="button"
                  disabled={saving}
                  onClick={() => {
                    if (window.confirm(ADMIN_COPY.contentFocus.turnOffConfirm)) {
                      void save({ ...draft, topics: [] })
                    }
                  }}
                  className="min-h-11 rounded-lg border border-slate-300 px-4 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                >
                  {ADMIN_COPY.contentFocus.turnOff}
                </button>
              )}
              <button
                type="button"
                disabled={saving || !changed || Boolean(period) || (draft.topics.length === 0 && !enabled)}
                onClick={() => void save(draft)}
                className="min-h-11 rounded-lg bg-blue-600 px-5 text-sm font-semibold text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {saving ? '저장 중…' : ADMIN_COPY.contentFocus.save}
              </button>
            </div>
          </fieldset>
        )}
      </div>
    </details>
  )
}
