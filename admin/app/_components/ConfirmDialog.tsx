'use client'

import { useEffect, useId, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

import { ADMIN_COPY } from '@/lib/admin-copy'
import { typedConfirmationMatches } from '@/lib/danger-confirm'

const COPY = ADMIN_COPY.dangerConfirm

/**
 * 위험 동작 확인창. `typedConfirmation`을 주면 그 글자(병원명·이메일 등)를 그대로 다시 입력해야
 * 확인 버튼이 열린다. 쓰기는 부르는 화면이 `onConfirm`에서 기존 `fetchAPI`(BFF actor 단언)로 한다.
 */
export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel,
  typedConfirmation,
  tone = 'danger',
  busy = false,
  error,
  onConfirm,
  onClose,
}: {
  open: boolean
  title: string
  description: string
  confirmLabel: string
  /** 이 글자를 다시 입력해야 확인할 수 있다. 없으면 버튼만 */
  typedConfirmation?: string | null
  tone?: 'danger' | 'neutral'
  busy?: boolean
  error?: string | null
  onConfirm: () => void
  onClose: () => void
}) {
  const [typed, setTyped] = useState('')
  const titleId = useId()
  const inputRef = useRef<HTMLInputElement>(null)
  const cancelRef = useRef<HTMLButtonElement>(null)
  const panelRef = useRef<HTMLDivElement>(null)

  // 부르는 화면은 매 렌더 새 onClose를 넘긴다(상담 요청 목록은 5초마다 다시 그린다).
  // 입력 초기화·포커스는 창이 열릴 때 한 번만 하고, 닫기는 최신 함수를 ref로 읽는다 —
  // 그렇지 않으면 입력하던 이름이 다시 그릴 때마다 지워진다.
  const latest = useRef({ onClose, busy })
  useEffect(() => {
    latest.current = { onClose, busy }
  })

  useEffect(() => {
    if (!open) return
    setTyped('')
    // 닫히면 연 자리(⋯ 메뉴 버튼 등)로 포커스를 돌려준다.
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const focusTarget = inputRef.current ?? cancelRef.current
    focusTarget?.focus()
    // 캡처 단계에서 먼저 받는다 — Escape가 뒤의 콘텐츠 상세까지 닫지 않게, Tab이 창 밖
    // (배경 버튼)으로 빠지지 않게 창 안에서만 돈다.
    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        event.stopPropagation()
        if (!latest.current.busy) latest.current.onClose()
        return
      }
      if (event.key !== 'Tab') return
      const focusables = Array.from(
        panelRef.current?.querySelectorAll<HTMLElement>('input, button:not([disabled])') ?? [],
      )
      if (focusables.length === 0) return
      const first = focusables[0]
      const last = focusables[focusables.length - 1]
      const active = document.activeElement
      if (!panelRef.current?.contains(active)) {
        event.preventDefault()
        first.focus()
      } else if (event.shiftKey && active === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && active === last) {
        event.preventDefault()
        first.focus()
      }
    }
    window.addEventListener('keydown', onKey, true)
    return () => {
      window.removeEventListener('keydown', onKey, true)
      opener?.focus()
    }
  }, [open])

  if (!open || typeof document === 'undefined') return null
  const matches = typedConfirmationMatches(typedConfirmation, typed)
  const confirmClass =
    tone === 'danger'
      ? 'bg-red-600 text-white hover:bg-red-700'
      : 'bg-slate-900 text-white hover:bg-slate-800'

  return createPortal(
    <div
      className="fixed inset-0 z-[60] flex items-start justify-center overflow-y-auto bg-slate-900/40 p-4"
      role="alertdialog"
      aria-modal="true"
      aria-labelledby={titleId}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !busy) onClose()
      }}
    >
      <div ref={panelRef} className="mt-24 w-full max-w-md rounded-xl bg-white p-5 shadow-xl">
        <h3 id={titleId} className="text-base font-bold text-slate-900">{title}</h3>
        <p className="mt-2 whitespace-pre-line break-keep text-sm leading-6 text-slate-600">{description}</p>
        {typedConfirmation && (
          <label className="mt-4 block text-xs font-medium text-slate-600">
            {COPY.typePrompt.replace('{value}', typedConfirmation)}
            <input
              ref={inputRef}
              value={typed}
              onChange={(event) => setTyped(event.target.value)}
              autoComplete="off"
              spellCheck={false}
              className="mt-1.5 block min-h-11 w-full rounded-lg border border-slate-300 px-3 text-sm text-slate-900 focus:border-red-400 focus:outline-none focus:ring-2 focus:ring-red-100"
            />
          </label>
        )}
        {error && (
          <p role="alert" className="mt-3 rounded-lg bg-red-50 p-3 text-sm leading-6 text-red-700 [word-break:keep-all]">
            {error}
          </p>
        )}
        <div className="mt-5 flex justify-end gap-2">
          <button
            ref={cancelRef}
            type="button"
            onClick={onClose}
            disabled={busy}
            className="min-h-11 rounded-lg border border-slate-300 bg-white px-4 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
          >
            {COPY.cancel}
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={busy || !matches}
            className={`min-h-11 rounded-lg px-4 text-sm font-semibold disabled:cursor-not-allowed disabled:opacity-40 ${confirmClass}`}
          >
            {busy ? COPY.busy : confirmLabel}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  )
}
