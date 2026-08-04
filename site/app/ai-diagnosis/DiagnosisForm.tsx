'use client'

import Link from 'next/link'
import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  EMPTY_FORM,
  type DiagnosisFormValues,
  type FieldErrors,
  confirmationRows,
  suggestEmailCorrection,
  toRequestPayload,
  validateDiagnosisForm,
} from '@/lib/diagnosis-form'

type SlotInfo = { total: number; used: number; remaining: number }

type Submission =
  | { phase: 'idle' }
  | { phase: 'confirming' }
  | { phase: 'sending' }
  | { phase: 'done'; statusUrl: string; slotNo: number }
  | { phase: 'error'; message: string }

const FIELDS: {
  name: keyof DiagnosisFormValues
  label: string
  placeholder: string
  hint?: string
  type?: string
}[] = [
  {
    name: 'clinicName',
    label: '정식 병원명',
    placeholder: '장편한외과의원',
    // "연세의원"과 "강남연세의원"은 다른 문자열이다 — 판정이 여기에 달려 있다.
    hint: '간판에 적힌 그대로, 의원·병원까지 정확히 입력해 주세요.',
  },
  { name: 'regionKeyword', label: '지역', placeholder: '수서역', hint: '지하철역 또는 동 이름' },
  { name: 'clinicType', label: '진료과', placeholder: '외과' },
  { name: 'clinicPhone', label: '병원 대표번호', placeholder: '02-123-4567' },
  {
    name: 'coreKeywords',
    label: '핵심 키워드',
    placeholder: '대장내시경, 치질',
    hint: '쉼표로 구분해 최대 4개. 병원명은 넣지 마세요.',
  },
  { name: 'contactName', label: '담당자 이름', placeholder: '홍길동' },
  { name: 'contact', label: '담당자 연락처', placeholder: '010-1234-5678' },
  {
    name: 'email',
    label: '리포트 받을 이메일',
    placeholder: 'doctor@example.com',
    type: 'email',
  },
]

export default function DiagnosisForm() {
  const [values, setValues] = useState<DiagnosisFormValues>(EMPTY_FORM)
  const [errors, setErrors] = useState<FieldErrors>({})
  const [submission, setSubmission] = useState<Submission>({ phase: 'idle' })
  const [slots, setSlots] = useState<SlotInfo | null>(null)

  // 남은 자리는 실제 카운터다. 희소성을 연출하려고 숫자를 꾸미지 않는다 —
  // 방법론 공개가 이 제품의 차별점인데 카운터를 꾸미면 그 주장이 무너진다.
  useEffect(() => {
    let cancelled = false
    fetch('/api/diagnosis/slots', { cache: 'no-store' })
      .then((response) => (response.ok ? response.json() : null))
      .then((data) => {
        if (!cancelled && data) setSlots(data)
      })
      .catch(() => undefined)
    return () => {
      cancelled = true
    }
  }, [])

  const emailSuggestion = useMemo(() => suggestEmailCorrection(values.email), [values.email])
  const soldOut = slots !== null && slots.remaining <= 0

  const update = useCallback((name: keyof DiagnosisFormValues, value: string | boolean) => {
    setValues((prev) => ({ ...prev, [name]: value }))
    setErrors((prev) => ({ ...prev, [name]: undefined }))
  }, [])

  function handleReview(event: React.FormEvent) {
    event.preventDefault()
    const found = validateDiagnosisForm(values)
    setErrors(found)
    if (Object.keys(found).length > 0) return
    // 바로 접수하지 않는다 — 확인 모달이 1회 제한 고지의 본체다.
    setSubmission({ phase: 'confirming' })
  }

  async function handleConfirm() {
    setSubmission({ phase: 'sending' })
    try {
      const response = await fetch('/api/diagnosis', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(toRequestPayload(values, '/ai-diagnosis')),
      })
      const data = await response.json().catch(() => ({}))
      if (!response.ok) {
        setSubmission({ phase: 'error', message: data?.error || '접수에 실패했습니다.' })
        return
      }
      setSubmission({
        phase: 'done',
        statusUrl: data.status_url,
        slotNo: data.slot_no,
      })
    } catch {
      setSubmission({
        phase: 'error',
        message: '접수 서버에 연결하지 못했습니다. 잠시 후 다시 시도해 주세요.',
      })
    }
  }

  if (submission.phase === 'done') {
    return (
      <div className="dg-panel dg-done">
        <h2>접수되었습니다</h2>
        <p>
          측정을 시작했습니다. 완료되면 <strong>{values.email}</strong> 으로 리포트를 보내드립니다.
        </p>
        {/* **언제 오는지**가 빠져 있었다. FAQ에는 "신청 후 15분 안에"라고 적어 두고
            정작 접수 직후 화면에서는 말하지 않아서, 기다리는 사람이 얼마나 기다려야
            하는지 모른 채로 남았다. 기다림의 길이를 모르면 짧아도 길게 느껴진다. */}
        <p>
          <strong>보통 15분 안에 도착합니다.</strong> ChatGPT와 Gemini에 실제로 18번 묻고
          답변을 세는 동안 이 창을 닫으셔도 됩니다.
        </p>
        <p className="dg-muted">
          메일이 오지 않아도 아래 주소에서 진행 상황과 결과를 확인하실 수 있습니다.
        </p>
        <a className="dg-status-link" href={submission.statusUrl}>
          {submission.statusUrl}
        </a>
      </div>
    )
  }

  return (
    <div className="dg-panel">
      {slots && (
        <p className="dg-slots" aria-live="polite">
          오늘 남은 자리 <strong>{slots.remaining}</strong> / {slots.total}
        </p>
      )}

      <p className="dg-once-notice">
        <strong>한 병원당 딱 한 번만 신청할 수 있습니다.</strong> 대표번호와 이메일 모두 재신청이
        불가능하니, 반드시 <strong>우리 병원</strong> 정보로 정확히 입력해 주세요.
      </p>

      <form onSubmit={handleReview} noValidate>
        {FIELDS.map((field) => (
          <div className="dg-field" key={field.name}>
            <label htmlFor={`dg-${field.name}`}>{field.label}</label>
            <input
              id={`dg-${field.name}`}
              name={field.name}
              type={field.type ?? 'text'}
              value={String(values[field.name] ?? '')}
              placeholder={field.placeholder}
              onChange={(event) => update(field.name, event.target.value)}
              aria-invalid={Boolean(errors[field.name])}
              aria-describedby={errors[field.name] ? `dg-${field.name}-error` : undefined}
              disabled={soldOut}
            />
            {field.hint && !errors[field.name] && <p className="dg-hint">{field.hint}</p>}
            {errors[field.name] && (
              <p className="dg-error" id={`dg-${field.name}-error`}>
                {errors[field.name]}
              </p>
            )}
            {field.name === 'email' && emailSuggestion && !errors.email && (
              <p className="dg-hint">
                혹시 <button type="button" onClick={() => update('email', emailSuggestion)}>
                  {emailSuggestion}
                </button>{' '}
                아닌가요?
              </p>
            )}
          </div>
        ))}

        <div className="dg-field dg-consent">
          <label>
            <input
              type="checkbox"
              checked={values.privacy}
              onChange={(event) => update('privacy', event.target.checked)}
              disabled={soldOut}
            />
            <span>
              개인정보 수집·이용에 동의합니다. (<Link href="/privacy">처리방침</Link>)
            </span>
          </label>
          {errors.privacy && <p className="dg-error">{errors.privacy}</p>}
        </div>

        {submission.phase === 'error' && <p className="dg-error dg-error-block">{submission.message}</p>}

        <button className="dg-submit" type="submit" disabled={soldOut}>
          {soldOut ? '오늘 접수 마감 — 내일 다시 열립니다' : '입력 정보 확인하기'}
        </button>
      </form>

      {submission.phase !== 'idle' && submission.phase !== 'error' && (
        <ConfirmDialog
          rows={confirmationRows(values)}
          sending={submission.phase === 'sending'}
          onCancel={() => setSubmission({ phase: 'idle' })}
          onConfirm={handleConfirm}
        />
      )}
    </div>
  )
}

/**
 * 정보 확인 모달 (PRD F1-8).
 *
 * 브라우저 `alert()`가 아니다 — 값을 표로 보여줘야 하고, alert은 서식이 불가능하며
 * 자동화 도구에서 브라우저를 멈춘다. **이 화면이 1회 제한 고지의 본체다.**
 */
function ConfirmDialog({
  rows,
  sending,
  onCancel,
  onConfirm,
}: {
  rows: { label: string; value: string }[]
  sending: boolean
  onCancel: () => void
  onConfirm: () => void
}) {
  return (
    <div className="dg-modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="dg-modal-title">
      <div className="dg-modal">
        <h2 id="dg-modal-title">이 정보가 정확한가요?</h2>
        <dl className="dg-modal-rows">
          {rows.map((row) => (
            <div key={row.label}>
              <dt>{row.label}</dt>
              <dd>{row.value || '—'}</dd>
            </div>
          ))}
        </dl>
        <p className="dg-modal-warning">
          ⚠ 한 병원당 딱 한 번만 신청할 수 있습니다. 대표번호와 이메일 모두 재신청이 불가능하니
          우리 병원 정보가 맞는지 꼭 확인해 주세요.
        </p>
        {/* 경고만 있고 보상이 없으면 확인 모달이 마지막 이탈 지점이 된다.
            "한 번뿐"이라는 부담 옆에 "15분이면 온다"는 기대를 같이 둔다. */}
        <p className="dg-modal-eta">리포트는 보통 15분 안에 이메일로 도착합니다.</p>
        <div className="dg-modal-actions">
          <button type="button" onClick={onCancel} disabled={sending}>
            수정하기
          </button>
          <button type="button" className="dg-primary" onClick={onConfirm} disabled={sending}>
            {sending ? '접수 중…' : '이 정보로 신청'}
          </button>
        </div>
      </div>
    </div>
  )
}
