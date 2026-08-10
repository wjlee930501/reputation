'use client'

import { useRef, useState } from 'react'
import Link from 'next/link'

import {
  developerSupportText,
  isExpectedClipboardFailure,
  type OperatorSurface,
} from '@/lib/operations-journey'

type Props = {
  readonly message: string
  readonly surface: OperatorSurface
  readonly onRetry?: () => void
  readonly retryLabel?: string
  readonly actionHref?: string
  readonly actionLabel?: string
}

export function OperatorIssuePanel({
  message,
  surface,
  onRetry,
  retryLabel = '다시 시도',
  actionHref,
  actionLabel,
}: Props) {
  const copyButton = useRef<HTMLButtonElement>(null)
  const [copyStatus, setCopyStatus] = useState('')

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(
        developerSupportText(surface, message, window.location.href),
      )
      setCopyStatus('개발팀 문의용 정보가 복사되었습니다.')
    } catch (error: unknown) {
      if (!isExpectedClipboardFailure(error)) throw error
      setCopyStatus('복사하지 못했습니다. 브라우저의 클립보드 권한을 확인해 주세요.')
    } finally {
      window.requestAnimationFrame(() => copyButton.current?.focus())
    }
  }

  return (
    <div role="alert" className="rounded-xl border border-[var(--color-revisit-red-50)] bg-[var(--color-revisit-red-20)] p-4 text-sm text-[var(--color-revisit-red-70)]">
      <p className="whitespace-pre-line break-keep leading-6">{message}</p>
      <div className="mt-3 flex flex-col gap-2 sm:flex-row">
        {actionHref && actionLabel ? (
          <Link href={actionHref} className="inline-flex min-h-11 items-center justify-center rounded-lg bg-[var(--color-revisit-red-70)] px-4 font-bold text-white">
            {actionLabel}
          </Link>
        ) : null}
        {onRetry ? (
          <button type="button" onClick={onRetry} className="min-h-11 rounded-lg bg-[var(--color-revisit-red-70)] px-4 font-bold text-white">
            {retryLabel}
          </button>
        ) : null}
        <button ref={copyButton} type="button" onClick={() => void copy()} className="min-h-11 rounded-lg border border-[var(--color-revisit-red-50)] bg-[var(--color-revisit-surface)] px-4 font-bold text-[var(--color-revisit-red-70)]">
          개발팀 문의용 정보 복사
        </button>
      </div>
      {copyStatus ? <p role="status" aria-live="polite" className="mt-2 text-sm font-semibold">{copyStatus}</p> : null}
    </div>
  )
}
