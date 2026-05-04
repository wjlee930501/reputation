'use client'

import { useParams } from 'next/navigation'
import { Dispatch, FormEvent, SetStateAction, useEffect, useMemo, useState } from 'react'
import { fetchAPI } from '@/lib/api'
import {
  AIQueryTarget,
  AIQueryTargetPriority,
  AIQueryTargetStatus,
  QUERY_TARGET_PRIORITY_LABELS,
  QUERY_TARGET_STATUS_LABELS,
} from '@/types'

type FormState = {
  name: string
  target_intent: string
  priority: AIQueryTargetPriority
  status: AIQueryTargetStatus
  target_month: string
  region_terms: string
  specialty: string
  condition_or_symptom: string
  treatment: string
  decision_criteria: string
  patient_language: string
  platforms: string
  competitor_names: string
  variants: string
}

const DEFAULT_FORM: FormState = {
  name: '',
  target_intent: '추천형',
  priority: 'NORMAL',
  status: 'ACTIVE',
  target_month: '',
  region_terms: '',
  specialty: '',
  condition_or_symptom: '',
  treatment: '',
  decision_criteria: '',
  patient_language: 'ko',
  platforms: 'CHATGPT\nGEMINI',
  competitor_names: '',
  variants: '',
}

export default function QueryTargetsPage() {
  const params = useParams<{ id: string }>()
  const hospitalId = params.id
  const [targets, setTargets] = useState<AIQueryTarget[]>([])
  const [form, setForm] = useState<FormState>(DEFAULT_FORM)
  const [variantDrafts, setVariantDrafts] = useState<Record<string, string>>({})
  const [variantSavingByTarget, setVariantSavingByTarget] = useState<Record<string, boolean>>({})
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const activeTargets = useMemo(() => targets.filter((target) => target.status !== 'ARCHIVED'), [targets])
  const archivedTargets = useMemo(() => targets.filter((target) => target.status === 'ARCHIVED'), [targets])

  useEffect(() => {
    loadTargets()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hospitalId])

  async function loadTargets() {
    setLoading(true)
    setError(null)
    try {
      const data = await fetchAPI(`/admin/hospitals/${hospitalId}/query-targets?include_archived=true`)
      setTargets(data ?? [])
    } catch (err) {
      setError(err instanceof Error ? err.message : 'AI 노출용 환자 질문 전략을 불러오지 못했습니다.')
    } finally {
      setLoading(false)
    }
  }

  async function handleCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setSaving(true)
    setError(null)
    try {
      await fetchAPI(`/admin/hospitals/${hospitalId}/query-targets`, {
        method: 'POST',
        body: JSON.stringify({
          name: form.name,
          target_intent: form.target_intent,
          priority: form.priority,
          status: form.status,
          target_month: form.target_month || null,
          region_terms: toList(form.region_terms),
          specialty: emptyToNull(form.specialty),
          condition_or_symptom: emptyToNull(form.condition_or_symptom),
          treatment: emptyToNull(form.treatment),
          decision_criteria: toList(form.decision_criteria),
          patient_language: form.patient_language || 'ko',
          platforms: toList(form.platforms),
          competitor_names: toList(form.competitor_names),
          created_by: 'MotionLabs Ops',
          updated_by: 'MotionLabs Ops',
          variants: toList(form.variants).map((query_text) => ({
            query_text,
            platform: firstPlatform(form.platforms),
            language: form.patient_language || 'ko',
            is_active: true,
          })),
        }),
      })
      setForm(DEFAULT_FORM)
      await loadTargets()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'AI 노출용 환자 질문 전략을 저장하지 못했습니다.')
    } finally {
      setSaving(false)
    }
  }

  async function patchTarget(target: AIQueryTarget, patch: Partial<AIQueryTarget>) {
    setError(null)
    try {
      await fetchAPI(`/admin/hospitals/${hospitalId}/query-targets/${target.id}`, {
        method: 'PATCH',
        body: JSON.stringify({ ...patch, updated_by: 'MotionLabs Ops' }),
      })
      await loadTargets()
    } catch (err) {
      setError(err instanceof Error ? err.message : '상태를 변경하지 못했습니다.')
    }
  }

  async function addVariant(target: AIQueryTarget) {
    const queryText = (variantDrafts[target.id] ?? '').trim()
    if (!queryText || variantSavingByTarget[target.id]) return
    const alreadyExists = target.variants.some(
      (variant) => variant.query_text.trim() === queryText && variant.platform === (target.platforms[0] ?? 'CHATGPT'),
    )
    if (alreadyExists) {
      setError('이미 등록된 질문 변형입니다.')
      return
    }
    setVariantSavingByTarget((prev) => ({ ...prev, [target.id]: true }))
    setError(null)
    try {
      await fetchAPI(`/admin/hospitals/${hospitalId}/query-targets/${target.id}/variants`, {
        method: 'POST',
        body: JSON.stringify({
          query_text: queryText,
          platform: target.platforms[0] ?? 'CHATGPT',
          language: target.patient_language || 'ko',
          is_active: true,
        }),
      })
      setVariantDrafts((prev) => ({ ...prev, [target.id]: '' }))
      await loadTargets()
    } catch (err) {
      setError(err instanceof Error ? err.message : '질문 변형을 추가하지 못했습니다.')
    } finally {
      setVariantSavingByTarget((prev) => ({ ...prev, [target.id]: false }))
    }
  }

  return (
    <main className="p-8 space-y-6 bg-slate-50 min-h-full">
      <section className="rounded-2xl bg-gradient-to-br from-slate-900 via-slate-800 to-blue-900 p-7 text-white shadow-sm">
        <p className="text-[11px] font-semibold uppercase tracking-[0.2em] text-blue-200">
          AI Exposure Strategy
        </p>
        <div className="mt-2 flex flex-col gap-5 lg:flex-row lg:items-end lg:justify-between">
          <div className="max-w-2xl">
            <h2 className="text-2xl font-bold">AI에 노출시킬 환자 질문</h2>
            <p className="mt-2 text-sm leading-6 text-blue-50/90">
              환자가 ChatGPT·Gemini 같은 AI 답변 서비스에서 병원을 찾을 때 어떤 질문에 우리 병원이 떠야 하는지 정의합니다.
              일반 검색 키워드가 아니라 AI 언급률 측정 → 노출 진단 → 환자 질문에 맞춘 콘텐츠 가이드 작성까지
              운영 흐름의 단위가 되는 타깃입니다.
            </p>
          </div>
          <div className="grid grid-cols-3 gap-2 text-center text-xs lg:min-w-[320px]">
            <SummaryPill
              label="운영중"
              value={String(activeTargets.filter((target) => target.status === 'ACTIVE').length)}
            />
            <SummaryPill
              label="일시정지"
              value={String(activeTargets.filter((target) => target.status === 'PAUSED').length)}
            />
            <SummaryPill label="보관" value={String(archivedTargets.length)} />
          </div>
        </div>
      </section>

      {error && (
        <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      )}

      <section className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_380px]">
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <div>
              <h3 className="text-lg font-semibold text-slate-900">타깃 질문 목록</h3>
              <p className="text-sm text-slate-500">우선순위가 높은 환자 질문부터 이번 달 콘텐츠와 측정 흐름에 연결합니다.</p>
            </div>
            <button
              type="button"
              onClick={loadTargets}
              className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
            >
              새로고침
            </button>
          </div>

          {loading ? (
            <div className="rounded-2xl border border-slate-200 bg-white p-8 text-center text-sm text-slate-500">
              AI에 노출시킬 환자 질문 목록을 불러오는 중입니다.
            </div>
          ) : targets.length === 0 ? (
            <div className="rounded-2xl border border-dashed border-blue-200 bg-white p-8 text-center">
              <h4 className="text-base font-semibold text-slate-900">아직 등록된 환자 질문이 없습니다.</h4>
              <p className="mt-2 text-sm text-slate-500">
                첫 환자 질문을 만들면 이후 첫 AI 언급률 측정, 부족한 부분 진단, 환자 질문에 맞춘 콘텐츠 기획의 기준으로 사용할 수 있습니다.
              </p>
            </div>
          ) : (
            <div className="space-y-4">
              {targets.map((target) => (
                <TargetCard
                  key={target.id}
                  target={target}
                  variantDraft={variantDrafts[target.id] ?? ''}
                  onVariantDraftChange={(value) => setVariantDrafts((prev) => ({ ...prev, [target.id]: value }))}
                  onAddVariant={() => addVariant(target)}
                  isAddingVariant={variantSavingByTarget[target.id] ?? false}
                  onStatusChange={(status) => patchTarget(target, { status })}
                  onPriorityChange={(priority) => patchTarget(target, { priority })}
                />
              ))}
            </div>
          )}
        </div>

        <aside className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm h-fit">
          <h3 className="text-lg font-semibold text-slate-900">새 환자 질문 만들기</h3>
          <p className="mt-1 text-sm text-slate-500">
            환자 질문을 먼저 정의하고, 실제 AI에 던질 질문 변형을 함께 입력합니다.
          </p>

          <form onSubmit={handleCreate} className="mt-5 space-y-4">
            <Input label="질문 이름" value={form.name} onChange={(value) => setFormValue(setForm, 'name', value)} placeholder="예: 강남 치질 수술 추천" required />
            <div className="grid grid-cols-2 gap-3">
              <Input label="의도" value={form.target_intent} onChange={(value) => setFormValue(setForm, 'target_intent', value)} placeholder="추천형" required />
              <Input label="대상 월" value={form.target_month} onChange={(value) => setFormValue(setForm, 'target_month', value)} placeholder="2026-06" />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <Select
                label="우선순위"
                value={form.priority}
                onChange={(value) => setFormValue(setForm, 'priority', value as AIQueryTargetPriority)}
                options={[['HIGH', '높음'], ['NORMAL', '보통'], ['LOW', '낮음']]}
              />
              <Select
                label="상태"
                value={form.status}
                onChange={(value) => setFormValue(setForm, 'status', value as AIQueryTargetStatus)}
                options={[['ACTIVE', '운영중'], ['PAUSED', '일시정지']]}
              />
            </div>
            <Input label="진료과" value={form.specialty} onChange={(value) => setFormValue(setForm, 'specialty', value)} placeholder="대장항문외과" />
            <div className="grid grid-cols-2 gap-3">
              <Input label="증상/질환" value={form.condition_or_symptom} onChange={(value) => setFormValue(setForm, 'condition_or_symptom', value)} placeholder="치질" />
              <Input label="치료/시술" value={form.treatment} onChange={(value) => setFormValue(setForm, 'treatment', value)} placeholder="치질 수술" />
            </div>
            <Textarea label="지역어" value={form.region_terms} onChange={(value) => setFormValue(setForm, 'region_terms', value)} placeholder="강남\n서초" />
            <Textarea label="선택 기준" value={form.decision_criteria} onChange={(value) => setFormValue(setForm, 'decision_criteria', value)} placeholder="통증 부담\n회복 기간\n전문의 경험" />
            <Textarea label="측정 대상 AI" value={form.platforms} onChange={(value) => setFormValue(setForm, 'platforms', value)} placeholder="CHATGPT\nGEMINI" />
            <Textarea label="경쟁 병원" value={form.competitor_names} onChange={(value) => setFormValue(setForm, 'competitor_names', value)} placeholder="경쟁 병원명을 줄바꿈으로 입력" />
            <Textarea label="초기 질문 변형" value={form.variants} onChange={(value) => setFormValue(setForm, 'variants', value)} placeholder="강남 치질 병원 추천\n치질 수술 어디가 좋아" />
            <Input label="언어" value={form.patient_language} onChange={(value) => setFormValue(setForm, 'patient_language', value)} placeholder="ko" />

            <button
              type="submit"
              disabled={saving || !form.name.trim() || !form.target_intent.trim() || toList(form.platforms).length === 0}
              className="w-full rounded-xl bg-blue-600 px-4 py-3 text-sm font-semibold text-white shadow-sm hover:bg-blue-700 disabled:cursor-not-allowed disabled:bg-slate-300"
            >
              {saving ? '저장 중...' : '환자 질문 저장'}
            </button>
          </form>
        </aside>
      </section>
    </main>
  )
}

function TargetCard({
  target,
  variantDraft,
  onVariantDraftChange,
  onAddVariant,
  isAddingVariant,
  onStatusChange,
  onPriorityChange,
}: {
  target: AIQueryTarget
  variantDraft: string
  onVariantDraftChange: (value: string) => void
  onAddVariant: () => void
  isAddingVariant: boolean
  onStatusChange: (status: AIQueryTargetStatus) => void
  onPriorityChange: (priority: AIQueryTargetPriority) => void
}) {
  const priority = QUERY_TARGET_PRIORITY_LABELS[target.priority]
  const status = QUERY_TARGET_STATUS_LABELS[target.status]
  const visibleVariants = target.variants.slice(0, 20)
  const hiddenVariantCount = Math.max(target.variants.length - visibleVariants.length, 0)

  return (
    <article className={`rounded-2xl border bg-white p-5 shadow-sm ${target.status === 'ARCHIVED' ? 'border-slate-200 opacity-70' : 'border-slate-200'}`}>
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h4 className="text-lg font-semibold text-slate-900">{target.name}</h4>
            <Badge label={priority.label} color={priority.color} />
            <Badge label={status.label} color={status.color} />
            {target.target_month && <Badge label={target.target_month} color="bg-indigo-50 text-indigo-700 border-indigo-200" />}
          </div>
          <p className="mt-2 text-sm text-slate-600">
            {target.target_intent} · {target.specialty || '진료과 미지정'} · {target.condition_or_symptom || target.treatment || '질환/치료 미지정'}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <select
            value={target.priority}
            onChange={(event) => onPriorityChange(event.target.value as AIQueryTargetPriority)}
            className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs text-slate-700"
          >
            <option value="HIGH">높음</option>
            <option value="NORMAL">보통</option>
            <option value="LOW">낮음</option>
          </select>
          <select
            value={target.status}
            onChange={(event) => onStatusChange(event.target.value as AIQueryTargetStatus)}
            className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs text-slate-700"
          >
            <option value="ACTIVE">운영중</option>
            <option value="PAUSED">일시정지</option>
            <option value="ARCHIVED">보관</option>
          </select>
        </div>
      </div>

      <div className="mt-4 grid gap-3 md:grid-cols-4">
        <InfoBlock label="지역" value={target.region_terms.join(', ') || '미지정'} />
        <InfoBlock label="측정 대상 AI" value={target.platforms.join(', ') || '미지정'} />
        <InfoBlock label="경쟁 병원" value={target.competitor_names.join(', ') || '미지정'} />
        <InfoBlock label="질문 변형" value={`${target.summary.active_variant_count}/${target.summary.variant_count}개 운영`} />
      </div>

      {target.decision_criteria.length > 0 && (
        <div className="mt-4 flex flex-wrap gap-2">
          {target.decision_criteria.map((criteria) => (
            <span key={criteria} className="rounded-full bg-slate-100 px-3 py-1 text-xs text-slate-600">
              {criteria}
            </span>
          ))}
        </div>
      )}

      <div className="mt-4 rounded-xl bg-slate-50 p-4">
        <div className="flex items-center justify-between gap-3">
          <p className="text-sm font-medium text-slate-700">실제 AI에 던질 질문 변형</p>
          <p className="text-xs text-slate-400">측정 매트릭스 연결 {target.summary.linked_query_matrix_count}개</p>
        </div>
        <div className="mt-3 space-y-2">
          {target.variants.length === 0 ? (
            <p className="text-sm text-slate-500">아직 등록된 질문 변형이 없습니다.</p>
          ) : (
            visibleVariants.map((variant) => (
              <div key={variant.id} className="flex items-center justify-between gap-3 rounded-lg bg-white px-3 py-2 text-sm">
                <span className={variant.is_active ? 'text-slate-800' : 'text-slate-400 line-through'}>{variant.query_text}</span>
                <span className="shrink-0 text-xs text-slate-400">{variant.platform}</span>
              </div>
            ))
          )}
          {hiddenVariantCount > 0 && (
            <p className="rounded-lg bg-white px-3 py-2 text-xs text-slate-500">
              나머지 {hiddenVariantCount}개 질문 변형은 목록 성능을 위해 접어두었습니다.
            </p>
          )}
        </div>
        <div className="mt-3 flex gap-2">
          <input
            value={variantDraft}
            onChange={(event) => onVariantDraftChange(event.target.value)}
            placeholder="새 질문 변형 추가"
            className="min-w-0 flex-1 rounded-lg border border-slate-200 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-100"
          />
          <button
            type="button"
            onClick={onAddVariant}
            disabled={!variantDraft.trim() || isAddingVariant}
            className="rounded-lg bg-slate-900 px-3 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:bg-slate-300"
          >
            {isAddingVariant ? '추가 중...' : '추가'}
          </button>
        </div>
      </div>
    </article>
  )
}

function SummaryPill({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl bg-white/10 px-4 py-3 backdrop-blur">
      <div className="text-lg font-bold text-white">{value}</div>
      <div className="mt-1 text-blue-100">{label}</div>
    </div>
  )
}

function Badge({ label, color }: { label: string; color: string }) {
  return <span className={`inline-flex rounded-full border px-2.5 py-1 text-xs font-medium ${color}`}>{label}</span>
}

function InfoBlock({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-slate-100 bg-slate-50 px-3 py-2">
      <div className="text-[11px] font-medium uppercase tracking-wide text-slate-400">{label}</div>
      <div className="mt-1 truncate text-sm text-slate-700" title={value}>{value}</div>
    </div>
  )
}

function Input({
  label,
  value,
  onChange,
  placeholder,
  required,
}: {
  label: string
  value: string
  onChange: (value: string) => void
  placeholder?: string
  required?: boolean
}) {
  return (
    <label className="block">
      <span className="text-xs font-medium text-slate-600">{label}</span>
      <input
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        required={required}
        className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-100"
      />
    </label>
  )
}

function Textarea({
  label,
  value,
  onChange,
  placeholder,
}: {
  label: string
  value: string
  onChange: (value: string) => void
  placeholder?: string
}) {
  return (
    <label className="block">
      <span className="text-xs font-medium text-slate-600">{label}</span>
      <textarea
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        rows={3}
        className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-100"
      />
    </label>
  )
}

function Select({
  label,
  value,
  onChange,
  options,
}: {
  label: string
  value: string
  onChange: (value: string) => void
  options: Array<[string, string]>
}) {
  return (
    <label className="block">
      <span className="text-xs font-medium text-slate-600">{label}</span>
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-100"
      >
        {options.map(([optionValue, labelText]) => (
          <option key={optionValue} value={optionValue}>{labelText}</option>
        ))}
      </select>
    </label>
  )
}

function toList(value: string): string[] {
  return value
    .split(/[\n,]/)
    .map((item) => item.trim())
    .filter(Boolean)
}

function firstPlatform(value: string): string {
  return toList(value)[0] ?? 'CHATGPT'
}

function emptyToNull(value: string): string | null {
  const trimmed = value.trim()
  return trimmed ? trimmed : null
}

function setFormValue<K extends keyof FormState>(
  setForm: Dispatch<SetStateAction<FormState>>,
  key: K,
  value: FormState[K],
) {
  setForm((prev) => ({ ...prev, [key]: value }))
}
