'use client'

import Link from 'next/link'
import { useParams } from 'next/navigation'
import { useCallback, useEffect, useMemo, useState } from 'react'

import { ConfirmDialog } from '@/app/_components/ConfirmDialog'
import { ADMIN_COPY } from '@/lib/admin-copy'
import { ApiError, fetchAPI } from '@/lib/api'
import {
  MEASUREMENT_GROUPS,
  QUESTION_MAX_LENGTH,
  addRow,
  countGroup,
  countRoles,
  readSaveProblems,
  removeRow,
  setRole,
  setText,
  shapeIssues,
  swapRotating,
  toSavePayload,
  type MeasurementGroup,
  type MeasurementRole,
  type MeasurementSetDraftResponse,
  type MeasurementSetResponse,
  type MeasurementSetRow,
} from '@/lib/measurement-set'

const COPY = ADMIN_COPY.measurementSet

function measurementWhen(value: string): string {
  return new Date(value).toLocaleDateString('ko-KR', { timeZone: 'Asia/Seoul', month: 'long', day: 'numeric' })
}

function periodLabel(value: string): string {
  const [year, month] = value.split('-')
  return `${year}년 ${Number(month)}월`
}

/** 콘텐츠 > 측정 키워드 — 병원마다 30개(그룹 A~D, 고정 20 + 교체 10)를 보고 고치고 교체 질문을 고른다. */
export default function MeasurementSetPage() {
  const { id } = useParams<{ id: string }>()
  const [server, setServer] = useState<MeasurementSetResponse | null>(null)
  const [rows, setRows] = useState<MeasurementSetRow[]>([])
  const [loadError, setLoadError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState<{ tone: 'success' | 'error'; text: string } | null>(null)
  const [shapeProblems, setShapeProblems] = useState<string[]>([])
  const [swapFrom, setSwapFrom] = useState<number | null>(null)
  const [confirmDraft, setConfirmDraft] = useState(false)

  const load = useCallback(async () => {
    setLoadError(null)
    try {
      const data = await fetchAPI<MeasurementSetResponse>(`/admin/hospitals/${id}/measurement-set`)
      setServer(data)
      setRows(data.rows)
      setShapeProblems(data.shape.problems)
    } catch {
      setLoadError(COPY.loadError)
    }
  }, [id])

  useEffect(() => {
    void load()
  }, [load])

  const dirty = useMemo(
    () => server !== null && JSON.stringify(toSavePayload(rows)) !== JSON.stringify(toSavePayload(server.rows)),
    [rows, server],
  )

  useEffect(() => {
    if (!dirty) return
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault()
      event.returnValue = COPY.unsavedLeave
    }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])

  const localIssues = shapeIssues(rows)
  const totals = countRoles(rows)

  async function loadDraft() {
    setConfirmDraft(false)
    setMessage(null)
    try {
      const draft = await fetchAPI<MeasurementSetDraftResponse>(`/admin/hospitals/${id}/measurement-set/draft`, {
        method: 'POST',
      })
      setRows(draft.rows)
      setShapeProblems(draft.shape.problems)
      setMessage({ tone: 'success', text: COPY.draftLoaded })
    } catch (error) {
      setMessage({ tone: 'error', text: error instanceof Error ? error.message : COPY.loadError })
    }
  }

  async function save() {
    setSaving(true)
    setMessage(null)
    try {
      const data = await fetchAPI<MeasurementSetResponse>(`/admin/hospitals/${id}/measurement-set`, {
        method: 'PUT',
        body: JSON.stringify(toSavePayload(rows)),
      })
      setServer(data)
      setRows(data.rows)
      setShapeProblems(data.shape.problems)
      setMessage({ tone: 'success', text: COPY.saved })
    } catch (error) {
      if (error instanceof ApiError && error.status === 422) {
        const { byIndex, shape } = readSaveProblems(error.detail)
        setRows((current) => current.map((row, index) => ({ ...row, problems: byIndex.get(index) ?? [] })))
        setShapeProblems(shape)
        setMessage({ tone: 'error', text: COPY.saveError })
      } else {
        setMessage({ tone: 'error', text: error instanceof Error ? error.message : COPY.saveError })
      }
    } finally {
      setSaving(false)
    }
  }

  function onRoleChange(index: number, value: string) {
    setSwapFrom(null)
    setRows((current) => setRole(current, index, value === 'candidate' ? null : (value as MeasurementRole)))
  }

  function onSwap(candidateIndex: number) {
    if (swapFrom === null) return
    setRows((current) => swapRotating(current, swapFrom, candidateIndex))
    setSwapFrom(null)
  }

  if (loadError) {
    return (
      <div className="space-y-3 p-6">
        <p className="text-sm text-red-700">{loadError}</p>
        <button type="button" className="rounded-md border px-3 py-1.5 text-sm" onClick={() => void load()}>
          다시 불러오기
        </button>
      </div>
    )
  }
  if (!server) return <div className="p-6 text-sm text-slate-500">불러오는 중…</div>

  return (
    <div className="space-y-5 p-6">
      <div className="flex flex-wrap items-center gap-3">
        <Link href={`/hospitals/${id}/content`} className="text-sm text-slate-500 hover:text-slate-800">
          ← {COPY.back}
        </Link>
        <h2 className="text-lg font-semibold text-slate-900">{COPY.title}</h2>
        <span className="flex-1" />
        <span className="text-xs text-slate-500">
          {COPY.nextMeasurement(measurementWhen(server.next_measurement_at), periodLabel(server.next_measurement_period))}
        </span>
      </div>
      <p className="max-w-3xl text-sm text-slate-600">{COPY.intro}</p>
      {server.current_period_frozen && (
        <p className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900">{COPY.frozenNotice}</p>
      )}

      <div className="flex flex-wrap items-center gap-2 rounded-xl border border-slate-200 bg-white p-3">
        <span className="text-sm text-slate-700">{COPY.counts(totals.fixed, totals.rotating, totals.candidates)}</span>
        <span className="flex-1" />
        <button
          type="button"
          className="rounded-md border border-slate-300 px-3 py-1.5 text-sm disabled:opacity-50"
          disabled={saving}
          onClick={() => (rows.length ? setConfirmDraft(true) : void loadDraft())}
        >
          {rows.length ? COPY.redraft : COPY.draft}
        </button>
        <button
          type="button"
          className="rounded-md border border-slate-300 px-3 py-1.5 text-sm disabled:opacity-50"
          disabled={!dirty || saving}
          onClick={() => {
            setRows(server.rows)
            setShapeProblems(server.shape.problems)
            setSwapFrom(null)
          }}
        >
          {COPY.discard}
        </button>
        <button
          type="button"
          className="rounded-md bg-slate-900 px-3 py-1.5 text-sm text-white disabled:opacity-50"
          disabled={!dirty || saving || localIssues.length > 0}
          onClick={() => void save()}
        >
          {saving ? COPY.saving : COPY.save}
        </button>
      </div>

      {message && (
        <p
          role={message.tone === 'error' ? 'alert' : 'status'}
          className={
            message.tone === 'error'
              ? 'rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800'
              : 'rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-900'
          }
        >
          {message.text}
        </p>
      )}
      {[...new Set([...localIssues, ...shapeProblems])].length > 0 && (
        <ul className="list-disc space-y-1 rounded-md border border-amber-300 bg-amber-50 py-2 pl-8 pr-3 text-sm text-amber-900">
          {[...new Set([...localIssues, ...shapeProblems])].map((issue) => (
            <li key={issue}>{issue}</li>
          ))}
        </ul>
      )}

      {!server.curated && server.legacy_questions.length > 0 && (
        <section className="space-y-2 rounded-xl border border-slate-200 bg-slate-50 p-4">
          <h3 className="text-sm font-semibold text-slate-800">{COPY.legacyTitle}</h3>
          <p className="text-xs text-slate-500">{COPY.legacyHint}</p>
          <ul className="list-disc space-y-0.5 pl-5 text-sm text-slate-700">
            {server.legacy_questions.map((question) => (
              <li key={question.target_id}>{question.text}</li>
            ))}
          </ul>
        </section>
      )}

      {rows.length === 0 && <p className="text-sm text-slate-500">{COPY.empty}</p>}

      {MEASUREMENT_GROUPS.map((group) => (
        <GroupSection
          key={group}
          group={group}
          rows={rows}
          target={server.plan[group]}
          swapFrom={swapFrom}
          locked={saving}
          onText={(index, text) => setRows((current) => setText(current, index, text))}
          onRole={onRoleChange}
          onRemove={(index) => {
            setSwapFrom(null)
            setRows((current) => removeRow(current, index))
          }}
          onAdd={() => setRows((current) => addRow(current, group))}
          onPickSwap={(index) => setSwapFrom((current) => (current === index ? null : index))}
          onSwap={onSwap}
        />
      ))}

      <section className="space-y-2 rounded-xl border border-slate-200 bg-white p-4">
        <h3 className="text-sm font-semibold text-slate-800">{COPY.rulesTitle}</h3>
        <ul className="list-disc space-y-1 pl-5 text-sm text-slate-600">
          {COPY.rules.map((rule) => (
            <li key={rule}>{rule}</li>
          ))}
        </ul>
      </section>

      <ConfirmDialog
        open={confirmDraft}
        title={COPY.redraft}
        description={COPY.redraftConfirm}
        confirmLabel={COPY.redraft}
        tone="neutral"
        onConfirm={() => void loadDraft()}
        onClose={() => setConfirmDraft(false)}
      />
    </div>
  )
}

function GroupSection({
  group,
  rows,
  target,
  swapFrom,
  locked,
  onText,
  onRole,
  onRemove,
  onAdd,
  onPickSwap,
  onSwap,
}: {
  group: MeasurementGroup
  rows: MeasurementSetRow[]
  target: { fixed: number; rotating: number }
  swapFrom: number | null
  locked: boolean
  onText: (index: number, text: string) => void
  onRole: (index: number, value: string) => void
  onRemove: (index: number) => void
  onAdd: () => void
  onPickSwap: (index: number) => void
  onSwap: (index: number) => void
}) {
  const meta = COPY.groups[group]
  const counts = countGroup(rows, group)
  const indexed = rows.map((row, index) => ({ row, index })).filter(({ row }) => row.group === group)
  return (
    <section className="space-y-3 rounded-xl border border-slate-200 bg-white p-4" aria-labelledby={`group-${group}`}>
      <div className="flex flex-wrap items-baseline gap-2">
        <h3 id={`group-${group}`} className="text-sm font-semibold text-slate-800">
          {group}. {meta.name}
        </h3>
        <span className="text-xs text-slate-500">{meta.hint}</span>
        <span className="flex-1" />
        <span className="text-xs text-slate-600">{COPY.groupCounts(counts.fixed, counts.rotating, target)}</span>
      </div>
      <fieldset disabled={locked} className="contents">
      <ul className="space-y-2">
        {indexed.map(({ row, index }) => {
          const roleValue = row.role ?? 'candidate'
          const swapTarget = swapFrom !== null && row.role === null
          return (
            <li key={index} className="space-y-1">
              <div className="flex flex-wrap items-center gap-2">
                <select
                  aria-label={COPY.roleHint}
                  className="rounded-md border border-slate-300 px-2 py-1 text-sm"
                  value={roleValue}
                  onChange={(event) => onRole(index, event.target.value)}
                >
                  <option value="FIXED">{COPY.roles.FIXED}</option>
                  {group !== 'A' && <option value="ROTATING">{COPY.roles.ROTATING}</option>}
                  <option value="candidate">{COPY.roles.candidate}</option>
                </select>
                <input
                  className="min-w-[16rem] flex-1 rounded-md border border-slate-300 px-2 py-1 text-sm"
                  value={row.text}
                  maxLength={QUESTION_MAX_LENGTH}
                  placeholder={COPY.placeholder}
                  aria-label={`${meta.name} 질문`}
                  onChange={(event) => onText(index, event.target.value)}
                />
                {row.role === 'ROTATING' && (
                  <button
                    type="button"
                    className={`rounded-md border px-2 py-1 text-xs ${swapFrom === index ? 'border-slate-900 bg-slate-900 text-white' : 'border-slate-300'}`}
                    onClick={() => onPickSwap(index)}
                  >
                    {swapFrom === index ? COPY.swapPick : COPY.roles.ROTATING}
                  </button>
                )}
                {swapTarget && (
                  <button type="button" className="rounded-md border border-slate-900 px-2 py-1 text-xs" onClick={() => onSwap(index)}>
                    {COPY.swap}
                  </button>
                )}
                <button type="button" className="rounded-md px-2 py-1 text-xs text-slate-500 hover:text-red-700" onClick={() => onRemove(index)}>
                  {COPY.remove}
                </button>
              </div>
              {(row.problems ?? []).length > 0 && (
                <p className="pl-1 text-xs text-red-700">{(row.problems ?? []).join(' · ')}</p>
              )}
            </li>
          )
        })}
      </ul>
      <button type="button" className="text-sm text-slate-600 hover:text-slate-900" onClick={onAdd}>
        + {COPY.addQuestion}
      </button>
      </fieldset>
    </section>
  )
}
