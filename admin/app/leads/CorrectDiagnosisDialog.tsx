'use client'

import { ManualDiagnosisForm } from '@/app/_components/ManualDiagnosisForm'
import type { ManualDiagnosisValues } from '@/lib/manual-diagnosis'

/**
 * 도입문의로 들어온 값이 틀렸을 때 고쳐 다시 만드는 창.
 *
 * `진단 생성(내부용)`과 다른 경로다. 그쪽은 **진단이 없는** 리드를 채우는 것이고,
 * 여기는 이미 만들어진(그리고 잘못된 값으로 측정까지 끝난) 진단을 갈음한다.
 * 옛 진단은 지우지 않는다 — 실제로 지출한 공급자 호출이 기록으로 남아야 한다.
 */
export function CorrectDiagnosisDialog({
  clinicName,
  leadId,
  initialValues,
  submitting,
  error,
  onSubmit,
  onClose,
}: {
  clinicName: string
  leadId: string
  initialValues: Partial<ManualDiagnosisValues>
  submitting: boolean
  error: string | null
  onSubmit: (payload: Record<string, unknown>) => void
  onClose: () => void
}) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-slate-900/40 p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="correct-diagnosis-title"
    >
      <div className="mt-8 w-full max-w-3xl rounded-xl bg-white p-5 shadow-xl sm:p-6">
        <h3 id="correct-diagnosis-title" className="text-lg font-bold text-slate-900">
          값 고쳐 다시 만들기 — {clinicName}
        </h3>
        <p className="mt-2 break-keep text-sm leading-6 text-slate-600">
          지금 진단은 기록으로 남기고 새 진단을 만듭니다. 원장님께 자동으로 발송되지 않으며,
          측정이 다시 돌아가므로 결과가 나오기까지 시간이 걸립니다.
        </p>

        {error && (
          <p className="mt-4 rounded-lg bg-red-50 p-3 text-sm leading-6 text-red-700 [word-break:keep-all]" role="alert">
            {error}
          </p>
        )}

        <div className="mt-4">
          <ManualDiagnosisForm
            leadId={leadId}
            initialValues={initialValues}
            submitting={submitting}
            onSubmit={onSubmit}
            onCancel={onClose}
          />
        </div>
      </div>
    </div>
  )
}
