'use client'

import Link from 'next/link'
import { useCallback, useEffect, useRef, useState } from 'react'
import { attributionEventParams, decorateSourcePath, type Attribution } from '@/lib/ad-attribution'
import { ensureAttributionCaptured } from '@/lib/ad-attribution-client'
import { trackEvent } from '@/lib/analytics'
import {
  EMPTY_INQUIRY,
  toInquiryFormData,
  validateInquiryForm,
  type InquiryFieldErrors,
  type InquiryFormValues,
} from '@/lib/inquiry-form'

type Submission =
  | { phase: 'idle' }
  | { phase: 'sending' }
  | { phase: 'done' }
  | { phase: 'error'; message: string }

const FIELDS: {
  name: Exclude<keyof InquiryFormValues, 'privacy' | 'question'>
  label: string
  placeholder: string
  hint?: string
  inputMode?: 'tel'
}[] = [
  {
    name: 'clinicName',
    label: '정식 병원명',
    placeholder: '장편한외과의원',
    // "연세의원"과 "강남연세의원"은 다른 문자열이다 — 진단 판정이 여기에 달려 있다.
    hint: '간판에 적힌 그대로, 의원·병원까지 정확히 입력해 주세요.',
  },
  { name: 'specialty', label: '진료과', placeholder: '외과' },
  { name: 'regionKeyword', label: '지역', placeholder: '수서역', hint: '지하철역 또는 동 이름' },
  {
    name: 'coreKeywords',
    label: '핵심 진료 키워드',
    placeholder: '대장내시경, 치질',
    hint: '쉼표로 구분해 최대 4개. 병원명은 넣지 마세요.',
  },
  { name: 'contactName', label: '원장님 성함', placeholder: '홍길동' },
  {
    name: 'contact',
    label: '연락처',
    placeholder: '010-1234-5678',
    hint: '접수 확인 문자를 이 번호로 보내드립니다.',
    inputMode: 'tel',
  },
]

/**
 * 도입 문의 폼.
 *
 * 셀프서브 무료 진단(`/ai-diagnosis`)과 다른 경로다. 여기서 받는 것은 신청이 아니라
 * **문의**이고, 접수되는 순간 백엔드가 초도 노출 진단을 만들어 둔다. 그래서 이메일도,
 * 선착순 자리도 묻지 않는다 — 결과는 이메일로 가는 것이 아니라 담당자가 들고 연락한다.
 *
 * 진료과·지역·핵심 키워드를 필수로 받는 이유가 그것이다. 셋이 모두 있어야 진단이
 * 만들어지고(`LeadCreate.diagnosis_input`), 없으면 문의만 쌓이고 연락할 근거가 없다.
 */
export default function InquiryForm() {
  const [values, setValues] = useState<InquiryFormValues>(EMPTY_INQUIRY)
  const [errors, setErrors] = useState<InquiryFieldErrors>({})
  const [submission, setSubmission] = useState<Submission>({ phase: 'idle' })

  const attribution = useRef<Attribution | null>(null)
  // `lead_form_start`는 한 번만 — 타이핑할 때마다 쏘면 시작 수가 타건 수가 된다.
  const started = useRef(false)
  // 이펙트는 StrictMode(개발)·리마운트에서 두 번 돈다. 조회를 두 번 세면 전환율이 반토막 난다.
  const viewed = useRef(false)
  // honeypot은 DOM에만 있다 — 본문을 손으로 만들면 값이 빠져 함정이 죽는다.
  const honeypot = useRef<HTMLInputElement>(null)

  useEffect(() => {
    attribution.current = ensureAttributionCaptured()
    if (viewed.current) return
    viewed.current = true
    trackEvent('lead_form_view', attributionEventParams(attribution.current))
  }, [])

  const update = useCallback(
    (name: keyof InquiryFormValues, value: string | boolean) => {
      if (!started.current) {
        started.current = true
        trackEvent('lead_form_start', attributionEventParams(attribution.current))
      }
      setValues((prev) => ({ ...prev, [name]: value }))
      setErrors((prev) => ({ ...prev, [name]: undefined }))
    },
    [],
  )

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault()
    if (submission.phase === 'sending') return
    const found = validateInquiryForm(values)
    setErrors(found)
    if (Object.keys(found).length > 0) return

    setSubmission({ phase: 'sending' })
    try {
      const body = toInquiryFormData(values, decorateSourcePath('/', attribution.current))
      body.set('website', honeypot.current?.value ?? '')
      const response = await fetch('/api/leads', {
        method: 'POST',
        headers: { Accept: 'application/json' },
        body,
      })
      const data = await response.json().catch(() => ({}))
      if (!response.ok) {
        // 거절된 제출은 리드가 아니다 — 여기서 전환을 쏘면 실패가 전부 리드로 집계된다.
        setSubmission({
          phase: 'error',
          message: data?.error || '문의를 접수하지 못했습니다. 잠시 후 다시 시도해 주세요.',
        })
        return
      }
      trackEvent('generate_lead', attributionEventParams(attribution.current))
      setSubmission({ phase: 'done' })
    } catch {
      setSubmission({
        phase: 'error',
        message: '접수 서버에 연결하지 못했습니다. 잠시 후 다시 시도해 주세요.',
      })
    }
  }

  if (submission.phase === 'done') {
    return (
      <div className="lead-form lead-form-done" role="status">
        <h3>문의가 접수되었습니다</h3>
        <p>
          남겨 주신 진료과·지역·키워드로 <strong>{values.clinicName.trim()}</strong>의 AI 노출
          진단을 시작했습니다. 결과가 정리되면 담당자가 <strong>{values.contact.trim()}</strong>으로
          연락드립니다.
        </p>
        <p className="lead-form-note">접수 확인 문자를 먼저 보내드립니다.</p>
      </div>
    )
  }

  const sending = submission.phase === 'sending'
  return (
    <form className="lead-form" onSubmit={handleSubmit} noValidate>
      {/* Honeypot — 봇이 자동으로 채우는 필드. 사람에게는 보이지 않는다. */}
      <div className="lead-form-honeypot" aria-hidden="true">
        <label htmlFor="website">website</label>
        <input
          id="website"
          name="website"
          type="text"
          ref={honeypot}
          tabIndex={-1}
          autoComplete="off"
        />
      </div>

      <div className="lead-form-grid">
        {FIELDS.map((field) => {
          const error = errors[field.name]
          return (
            <div className="lead-field" key={field.name}>
              <label htmlFor={`lead-${field.name}`}>{field.label}</label>
              <input
                id={`lead-${field.name}`}
                type="text"
                inputMode={field.inputMode}
                value={values[field.name]}
                placeholder={field.placeholder}
                onChange={(event) => update(field.name, event.target.value)}
                aria-invalid={error ? true : undefined}
                aria-describedby={error ? `lead-${field.name}-error` : undefined}
                disabled={sending}
              />
              {error ? (
                <p className="lead-error" id={`lead-${field.name}-error`}>
                  {error}
                </p>
              ) : (
                field.hint && <p className="lead-hint">{field.hint}</p>
              )}
            </div>
          )
        })}
      </div>

      <div className="lead-field lead-field-wide">
        <label htmlFor="lead-question">문의 내용</label>
        <textarea
          id="lead-question"
          rows={3}
          maxLength={1000}
          value={values.question}
          placeholder="도입 절차와 비용이 궁금합니다."
          onChange={(event) => update('question', event.target.value)}
          aria-invalid={errors.question ? true : undefined}
          aria-describedby={errors.question ? 'lead-question-error' : undefined}
          disabled={sending}
        />
        {errors.question && (
          <p className="lead-error" id="lead-question-error">
            {errors.question}
          </p>
        )}
      </div>

      <div className="lead-consent">
        <label>
          <input
            type="checkbox"
            checked={values.privacy}
            onChange={(event) => update('privacy', event.target.checked)}
            disabled={sending}
          />
          <span>
            개인정보 수집·이용에 동의합니다. (<Link href="/privacy">처리방침</Link>)
          </span>
        </label>
        {errors.privacy && <p className="lead-error">{errors.privacy}</p>}
      </div>

      {submission.phase === 'error' && (
        <p className="lead-error lead-error-block" role="alert">
          {submission.message}
        </p>
      )}

      <button className="btn btn-primary btn-lg lead-submit" type="submit" disabled={sending}>
        {sending ? '접수 중…' : '도입 문의하기'}
      </button>
    </form>
  )
}
