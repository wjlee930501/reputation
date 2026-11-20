'use client'

import { useEffect, useState } from 'react'

import { ADMIN_COPY } from '@/lib/admin-copy'
import { isExpectedClipboardFailure } from '@/lib/operations-journey'
import type { PublicAddress } from '@/lib/public-address'

const COPY = ADMIN_COPY.address

/**
 * 주소 한 줄: 열리는 주소는 새 탭 링크, 옆에 복사 버튼. 아직 열리지 않는 주소는 글자로만
 * 보이고 복사는 된다(DNS 안내 등에 붙여 넣을 수 있게). 2026-11-20 마케팅 요청 — 모든 주소
 * 링크(새 탭) + 복사.
 */
export function AddressLink({
  address,
  label,
  className = '',
}: {
  address: PublicAddress
  label?: string
  className?: string
}) {
  return (
    <span className={`inline-flex min-w-0 items-center gap-1.5 ${className}`}>
      {label && <span className="shrink-0 text-slate-500">{label}</span>}
      {address.open ? (
        <a
          href={address.url}
          target="_blank"
          rel="noopener noreferrer"
          className="truncate font-medium text-blue-700 underline-offset-2 hover:underline"
          title={`${address.host} · ${COPY.opensInNewTab}`}
        >
          {address.host} <span aria-hidden>↗</span>
        </a>
      ) : (
        <span className="truncate text-slate-700" title={`${address.host} · ${COPY.notOpenYet}`}>
          {address.host}
        </span>
      )}
      <CopyAddressButton value={address.url} />
    </span>
  )
}

/** 주소 문자열만 복사하는 작은 버튼(링크는 화면이 따로 그릴 때). */
export function CopyAddressButton({ value }: { value: string }) {
  const [status, setStatus] = useState<'idle' | 'copied' | 'failed'>('idle')

  useEffect(() => {
    if (status === 'idle') return
    const timer = window.setTimeout(() => setStatus('idle'), 2000)
    return () => window.clearTimeout(timer)
  }, [status])

  async function copy() {
    try {
      await navigator.clipboard.writeText(value)
      setStatus('copied')
    } catch (error: unknown) {
      if (!isExpectedClipboardFailure(error)) throw error
      setStatus('failed')
    }
  }

  return (
    <button
      type="button"
      onClick={() => void copy()}
      aria-label={`${value} ${COPY.copy}`}
      className="inline-flex min-h-8 shrink-0 items-center rounded-md border border-slate-200 bg-white px-2 text-[11px] font-medium text-slate-600 hover:bg-slate-50"
    >
      {status === 'copied' ? COPY.copied : status === 'failed' ? COPY.copyFailed : COPY.copy}
    </button>
  )
}
