'use client'

import { OperatorIssuePanel } from '@/app/_components/OperatorIssuePanel'
import { safeOperatorError } from '@/lib/operations-journey'

export default function GlobalError({
  reset,
}: {
  error: Error & { digest?: string }
  reset: () => void
}) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-50 p-4">
      <div className="w-full max-w-xl">
        <h2 className="mb-4 text-xl font-bold text-slate-800">운영 화면을 불러오지 못했습니다</h2>
        <OperatorIssuePanel
          message={safeOperatorError('admin', '운영 화면 다시 불러오기를 누르세요.')}
          surface="admin"
          onRetry={reset}
          retryLabel="운영 화면 다시 불러오기"
        />
      </div>
    </div>
  )
}
