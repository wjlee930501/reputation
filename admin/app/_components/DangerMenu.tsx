'use client'

import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties } from 'react'
import { createPortal } from 'react-dom'

import { ADMIN_COPY } from '@/lib/admin-copy'

export interface DangerMenuItem {
  label: string
  onSelect: () => void
  danger?: boolean
  disabled?: boolean
  title?: string
}

const MENU_GAP = 6
const MENU_ESTIMATED_HEIGHT = 56

/**
 * ⋯ 메뉴. 일시정지·파기·정지 같은 위험 동작을 평소 버튼 줄에서 빼서 한 번 더 열어야 보이게
 * 한다(2026-11-20 마케팅 요청). 항목을 고르면 메뉴는 닫히고, 확인은 `ConfirmDialog`가 한다.
 *
 * 메뉴는 body에 띄운다 — 콘텐츠 상세처럼 `overflow-hidden` 안의 하단 버튼 줄에서 열면 잘린다.
 * 아래 공간이 모자라면 위로 펼친다. Escape는 메뉴만 닫고 바깥 창(콘텐츠 상세)까지 전하지 않는다.
 */
export function DangerMenu({ items, label = ADMIN_COPY.dangerConfirm.more }: { items: DangerMenuItem[]; label?: string }) {
  const [open, setOpen] = useState(false)
  const [position, setPosition] = useState<CSSProperties | null>(null)
  const buttonRef = useRef<HTMLButtonElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)

  useLayoutEffect(() => {
    if (!open) return
    function place() {
      const rect = buttonRef.current?.getBoundingClientRect()
      if (!rect) return
      const menuHeight = menuRef.current?.offsetHeight ?? MENU_ESTIMATED_HEIGHT * items.length
      const right = Math.max(8, window.innerWidth - rect.right)
      const below = rect.bottom + MENU_GAP + menuHeight <= window.innerHeight
      setPosition(
        below
          ? { position: 'fixed', top: rect.bottom + MENU_GAP, right }
          : { position: 'fixed', bottom: window.innerHeight - rect.top + MENU_GAP, right },
      )
    }
    place()
    window.addEventListener('resize', place)
    window.addEventListener('scroll', place, true)
    return () => {
      window.removeEventListener('resize', place)
      window.removeEventListener('scroll', place, true)
    }
  }, [open, items.length])

  useEffect(() => {
    if (!open) return
    function onDown(event: MouseEvent) {
      const target = event.target as Node
      if (!buttonRef.current?.contains(target) && !menuRef.current?.contains(target)) setOpen(false)
    }
    // 캡처 단계에서 먼저 받아 멈춘다 — 콘텐츠 상세의 Escape(닫기)까지 가지 않게.
    function onKey(event: KeyboardEvent) {
      if (event.key !== 'Escape') return
      event.stopPropagation()
      setOpen(false)
      buttonRef.current?.focus()
    }
    window.addEventListener('mousedown', onDown)
    window.addEventListener('keydown', onKey, true)
    return () => {
      window.removeEventListener('mousedown', onDown)
      window.removeEventListener('keydown', onKey, true)
    }
  }, [open])

  if (items.length === 0) return null
  return (
    <>
      <button
        ref={buttonRef}
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={label}
        onClick={() => setOpen((current) => !current)}
        className="inline-flex min-h-11 min-w-11 items-center justify-center rounded-lg border border-slate-300 bg-white px-2 text-base font-bold leading-none text-slate-600 hover:bg-slate-50"
      >
        ⋯
      </button>
      {open && typeof document !== 'undefined' && createPortal(
        <div
          ref={menuRef}
          role="menu"
          style={position ?? { position: 'fixed', visibility: 'hidden' }}
          className="z-[70] min-w-48 rounded-xl border border-slate-200 bg-white p-1 shadow-xl"
        >
          {items.map((item) => (
            <button
              key={item.label}
              type="button"
              role="menuitem"
              disabled={item.disabled}
              title={item.title}
              onClick={() => {
                setOpen(false)
                item.onSelect()
              }}
              className={`flex min-h-11 w-full items-center rounded-lg px-3 text-left text-sm disabled:cursor-not-allowed disabled:opacity-40 ${
                item.danger ? 'font-semibold text-red-600 hover:bg-red-50' : 'text-slate-700 hover:bg-slate-50'
              }`}
            >
              {item.label}
            </button>
          ))}
        </div>,
        document.body,
      )}
    </>
  )
}
