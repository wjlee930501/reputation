'use client'

import { useState } from 'react'
import {
  EMPTY_MANUAL_DIAGNOSIS,
  toManualDiagnosisPayload,
  validateManualDiagnosis,
  type ManualDiagnosisErrors,
  type ManualDiagnosisValues,
} from '@/lib/manual-diagnosis'

export type ManualDiagnosisResult = {
  readonly leadId: string
  readonly diagnosisId: string
  readonly supersededDiagnosisId: string | null
}

const FIELDS: {
  name: Exclude<keyof ManualDiagnosisValues, 'reason'>
  label: string
  placeholder: string
  hint?: string
}[] = [
  {
    name: 'clinicName',
    label: '정식 병원명',
    placeholder: '장편한외과의원',
    // "연세의원"과 "강남연세의원"은 다른 문자열이고, 언급 판정이 여기에 달려 있다.
    hint: '간판에 적힌 그대로. 이 문자열로 언급 여부를 판정합니다.',
  },
  { name: 'specialty', label: '진료과', placeholder: '정형외과' },
  { name: 'regionKeyword', label: '지역', placeholder: '강남역', hint: '지하철역 또는 동 이름' },
  {
    name: 'coreKeywords',
    label: '핵심 진료 키워드',
    placeholder: '도수치료, 허리통증',
    hint: '쉼표로 구분해 최대 4개. 병원명은 넣지 마세요.',
  },
  { name: 'contact', label: '연락처', placeholder: '02-123-4567', hint: '병원 대표번호도 괜찮습니다.' },
  { name: 'contactName', label: '원장님 성함', placeholder: '홍길동' },
]

/**
 * 콜용 노출 진단을 사람이 직접 만드는 폼.
 *
 * `leadId`가 있으면 그 상담 요청의 값을 고쳐 다시 만드는 경로다 — 서버가 기존 활성
 * 진단을 갈음하고 새로 만든다. 없으면 문의가 없는 병원을 새로 진단한다.
 */
export function ManualDiagnosisForm({
  initialValues,
  leadId,
  submitting,
  onSubmit,
  onCancel,
}: {
  initialValues?: Partial<ManualDiagnosisValues>
  leadId?: string
  submitting: boolean
  onSubmit: (payload: ReturnType<typeof toManualDiagnosisPayload>) => void
  onCancel?: () => void
}) {
  const [values, setValues] = useState<ManualDiagnosisValues>({
    ...EMPTY_MANUAL_DIAGNOSIS,
    ...initialValues,
  })
  const [errors, setErrors] = useState<ManualDiagnosisErrors>({})

  function update(name: keyof ManualDiagnosisValues, value: string) {
    setValues((prev) => ({ ...prev, [name]: value }))
    setErrors((prev) => ({ ...prev, [name]: undefined }))
  }

  function handleSubmit(event: React.FormEvent) {
    event.preventDefault()
    if (submitting) return
    // 기존 상담 요청에는 이미 연락처가 있다.
    const found = validateManualDiagnosis(values, { requireContact: leadId === undefined })
    setErrors(found)
    if (Object.keys(found).length > 0) return
    onSubmit(toManualDiagnosisPayload(values, leadId))
  }

  return (
    <form onSubmit={handleSubmit} noValidate>
      <div className="grid gap-4 sm:grid-cols-2">
        {FIELDS.map((field) => {
          const error = errors[field.name]
          const id = `manual-${field.name}`
          return (
            <div key={field.name}>
              <label htmlFor={id} className="block text-sm font-semibold text-slate-700">
                {field.label}
              </label>
              <input
                id={id}
                type="text"
                value={values[field.name]}
                placeholder={field.placeholder}
                onChange={(event) => update(field.name, event.target.value)}
                disabled={submitting}
                aria-invalid={error ? true : undefined}
                aria-describedby={error ? `${id}-error` : undefined}
                className={`mt-1 min-h-11 w-full rounded-lg border px-3 text-sm disabled:bg-slate-50 ${
                  error ? 'border-red-400' : 'border-slate-200'
                }`}
              />
              {error ? (
                <p id={`${id}-error`} className="mt-1 text-xs leading-5 text-red-600 [word-break:keep-all]">
                  {error}
                </p>
              ) : (
                field.hint && (
                  <p className="mt-1 text-xs leading-5 text-slate-500 [word-break:keep-all]">{field.hint}</p>
                )
              )}
            </div>
          )
        })}
      </div>

      <div className="mt-4">
        <label htmlFor="manual-reason" className="block text-sm font-semibold text-slate-700">
          직접 만드는 이유
        </label>
        <p className="mt-1 text-xs leading-5 text-slate-500 [word-break:keep-all]">
          기록에 그대로 남습니다. 기존 진단을 갈음하는 경우 무엇이 잘못됐는지 적어 주세요.
        </p>
        <textarea
          id="manual-reason"
          rows={2}
          maxLength={200}
          value={values.reason}
          placeholder="원장님이 진료과를 내과로 잘못 적어 정형외과로 다시 측정"
          onChange={(event) => update('reason', event.target.value)}
          disabled={submitting}
          aria-invalid={errors.reason ? true : undefined}
          aria-describedby={errors.reason ? 'manual-reason-error' : undefined}
          className={`mt-2 w-full rounded-lg border p-3 text-sm disabled:bg-slate-50 ${
            errors.reason ? 'border-red-400' : 'border-slate-200'
          }`}
        />
        {errors.reason && (
          <p id="manual-reason-error" className="mt-1 text-xs leading-5 text-red-600">
            {errors.reason}
          </p>
        )}
      </div>

      <div className="mt-5 flex flex-col gap-2 sm:flex-row">
        <button
          type="submit"
          disabled={submitting}
          className="min-h-11 rounded-lg bg-blue-600 px-5 text-sm font-bold text-white disabled:opacity-50"
        >
          {submitting ? '만드는 중' : '진단 만들기'}
        </button>
        {onCancel && (
          <button
            type="button"
            onClick={onCancel}
            disabled={submitting}
            className="min-h-11 rounded-lg border border-slate-200 bg-white px-5 text-sm font-bold text-slate-600 disabled:opacity-50"
          >
            취소
          </button>
        )}
      </div>
    </form>
  )
}
