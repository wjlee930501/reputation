'use client'

import { useEffect, useState } from 'react'

import { ADMIN_COPY } from '@/lib/admin-copy'
import { ApiError, fetchAPI } from '@/lib/api'
import { isExpectedOperatorRequestFailure, safeOperatorError } from '@/lib/operations-journey'
import type { Hospital } from '@/types'

const COPY = ADMIN_COPY.pageCopy
const CARD_SLOTS = [0, 1, 2] as const

interface Draft {
  title: string
  lede: string
  cards: Array<{ title: string; body: string }>
  parking: string
  transit: string
}

function draftFrom(hospital: Hospital | null): Draft {
  const principles = hospital?.care_principles ?? null
  return {
    title: principles?.title ?? '',
    lede: principles?.lede ?? '',
    cards: CARD_SLOTS.map((index) => ({
      title: principles?.cards?.[index]?.title ?? '',
      body: principles?.cards?.[index]?.body ?? '',
    })),
    parking: hospital?.parking_note ?? '',
    transit: hospital?.transit_note ?? '',
  }
}

/** 서버 검증 문장 앞의 칸 이름·'Value error,'를 걷어 마케터가 읽을 문장만 남긴다. */
function readableSaveError(message: string): string {
  return message
    .split('\n')
    .map((line) => line.replace(/^[a-z_.0-9]+: /, '').replace(/^Value error, /, ''))
    .join('\n')
}

/** 제목·설명 중 한쪽만 쓴 카드 번호(1부터). 서버도 같은 이유로 거절한다. */
function unpairedCard(draft: Draft): number | null {
  const index = draft.cards.findIndex((card) => Boolean(card.title.trim()) !== Boolean(card.body.trim()))
  return index < 0 ? null : index + 1
}

const inputClass =
  'w-full rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-800 focus:border-blue-500 focus:outline-none'

/**
 * 병원 정보 화면의 '병원 페이지 문구'. 진료 원칙(제목·소개 문장·카드 3개)과 주차·대중교통 안내를
 * 병원마다 쓴다(2026-11-17 마케팅 요청). 이 칸만 따로 저장한다 — 다른 섹션의 스냅샷을 함께
 * 보내지 않는다. 금지 표현 검사와 빈 칸 정리는 서버(`services/clinic_page_copy.py`)가 한다.
 */
export function PageCopySection({
  hospital,
  hospitalId,
  onSaved,
}: {
  hospital: Hospital | null
  hospitalId: string
  onSaved: () => void
}) {
  const savedKey = JSON.stringify(draftFrom(hospital))
  const [draft, setDraft] = useState<Draft>(() => draftFrom(hospital))
  // 사람이 고치는 중에는 서버 값으로 덮지 않는다 — 다른 섹션 저장 뒤 재조회가 늦게 와도 입력이 남는다.
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [feedback, setFeedback] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!dirty) setDraft(JSON.parse(savedKey) as Draft)
  }, [savedKey, dirty])

  function update(change: (current: Draft) => Draft) {
    setDirty(true)
    setFeedback(null)
    setDraft(change)
  }

  async function save() {
    setFeedback(null)
    const unpaired = unpairedCard(draft)
    if (unpaired !== null) {
      setError(COPY.cardPairMissing.replace('{n}', String(unpaired)))
      return
    }
    setSaving(true)
    setError(null)
    try {
      await fetchAPI(`/admin/hospitals/${hospitalId}/profile`, {
        method: 'PATCH',
        body: JSON.stringify({
          care_principles: {
            title: draft.title,
            lede: draft.lede,
            cards: draft.cards.map((card) => ({ title: card.title, body: card.body })),
          },
          parking_note: draft.parking,
          transit_note: draft.transit,
        }),
      })
      setDirty(false)
      setFeedback(COPY.saved)
      onSaved()
    } catch (cause: unknown) {
      if (!isExpectedOperatorRequestFailure(cause)) throw cause
      setError(
        cause instanceof ApiError && cause.message
          ? readableSaveError(cause.message)
          : safeOperatorError('onboarding', '잠시 뒤 다시 저장해 주세요.'),
      )
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="space-y-4 rounded-xl border border-slate-200 p-4">
      <div>
        <h4 className="text-sm font-semibold text-slate-800">{COPY.title}</h4>
        <p className="mt-0.5 break-keep text-xs text-slate-500">{COPY.hint}</p>
      </div>

      <label className="block space-y-1">
        <span className="text-xs font-semibold text-slate-700">{COPY.principlesTitle}</span>
        <input
          className={inputClass}
          value={draft.title}
          maxLength={60}
          placeholder={COPY.blankPlaceholder}
          onChange={(event) => update((current) => ({ ...current, title: event.target.value }))}
        />
      </label>

      <label className="block space-y-1">
        <span className="text-xs font-semibold text-slate-700">{COPY.principlesLede}</span>
        <textarea
          className={inputClass}
          rows={2}
          value={draft.lede}
          maxLength={300}
          placeholder={COPY.blankPlaceholder}
          onChange={(event) => update((current) => ({ ...current, lede: event.target.value }))}
        />
        <span className="block break-keep text-xs text-slate-500">{COPY.principlesLedeHint}</span>
      </label>

      <div className="space-y-3">
        <p className="break-keep text-xs text-slate-500">{COPY.cardHint}</p>
        {CARD_SLOTS.map((index) => (
          <fieldset key={index} className="space-y-2 rounded-lg border border-slate-200 p-3">
            <legend className="px-1 text-xs font-semibold text-slate-700">
              {COPY.card.replace('{n}', String(index + 1))}
            </legend>
            <input
              className={inputClass}
              aria-label={`${COPY.card.replace('{n}', String(index + 1))} ${COPY.cardTitle}`}
              value={draft.cards[index].title}
              maxLength={60}
              placeholder={`${COPY.cardTitle} — ${COPY.blankPlaceholder}`}
              onChange={(event) =>
                update((current) => ({
                  ...current,
                  cards: current.cards.map((card, cardIndex) =>
                    cardIndex === index ? { ...card, title: event.target.value } : card,
                  ),
                }))
              }
            />
            <textarea
              className={inputClass}
              aria-label={`${COPY.card.replace('{n}', String(index + 1))} ${COPY.cardBody}`}
              rows={2}
              value={draft.cards[index].body}
              maxLength={300}
              placeholder={`${COPY.cardBody} — ${COPY.blankPlaceholder}`}
              onChange={(event) =>
                update((current) => ({
                  ...current,
                  cards: current.cards.map((card, cardIndex) =>
                    cardIndex === index ? { ...card, body: event.target.value } : card,
                  ),
                }))
              }
            />
          </fieldset>
        ))}
      </div>

      <label className="block space-y-1">
        <span className="text-xs font-semibold text-slate-700">{COPY.parking}</span>
        <input
          className={inputClass}
          value={draft.parking}
          maxLength={200}
          placeholder={COPY.parkingPlaceholder}
          onChange={(event) => update((current) => ({ ...current, parking: event.target.value }))}
        />
      </label>

      <label className="block space-y-1">
        <span className="text-xs font-semibold text-slate-700">{COPY.transit}</span>
        <input
          className={inputClass}
          value={draft.transit}
          maxLength={200}
          placeholder={COPY.transitPlaceholder}
          onChange={(event) => update((current) => ({ ...current, transit: event.target.value }))}
        />
      </label>

      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={() => void save()}
          disabled={!dirty || saving}
          className="inline-flex items-center rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-60"
        >
          {saving ? COPY.saving : COPY.save}
        </button>
      </div>
      {feedback && <p className="text-sm font-semibold text-green-700">{feedback}</p>}
      {error && <p className="text-sm font-semibold text-red-700">{error}</p>}
    </div>
  )
}
