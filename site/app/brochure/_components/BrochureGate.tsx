'use client'

import Link from 'next/link'
import { useEffect, useRef, useState } from 'react'

import { ensureAttributionCaptured } from '@/lib/ad-attribution-client'
import { attributionEventParams, serializeAttribution, type Attribution } from '@/lib/ad-attribution'
import { trackEvent } from '@/lib/analytics'

type Values = { hospitalName: string; directorName: string; phone: string; consent: boolean }
type Errors = Partial<Record<keyof Values | 'form', string>>

const EMPTY: Values = { hospitalName: '', directorName: '', phone: '', consent: false }
const PHONE = /^01[016789][ -]?\d{3,4}[ -]?\d{4}$/

function validate(values: Values): Errors {
  const errors: Errors = {}
  if (!values.hospitalName.trim()) errors.hospitalName = '병원명을 입력해 주세요.'
  if (!values.directorName.trim()) errors.directorName = '원장님 성함을 입력해 주세요.'
  if (!values.phone.trim()) errors.phone = '휴대폰 번호를 입력해 주세요.'
  else if (!PHONE.test(values.phone.trim())) errors.phone = '휴대폰 번호 형식을 확인해 주세요. (예: 010-1234-5678)'
  if (!values.consent) errors.consent = '소개서 열람을 위해 동의해 주세요.'
  return errors
}

/**
 * 소개서 열람 게이트 — 세 칸과 동의 하나.
 *
 * 도입문의(여덟 칸)보다 확실히 가벼워야 낮은 사다리가 된다. 진료과·지역·키워드는 묻지
 * 않는다: 그건 진단 리포트를 만들 재료이고, 리포트는 도입문의의 몫이다.
 *
 * 동의는 체크박스 하나에 한 줄로 받되 **열람 기록을 쓴다는 사실은 그 한 줄 안에 적는다.**
 * 짧게 쓰는 것과 가리는 것은 다르다 — 미리 체크해 두거나 다른 문구에 섞으면 동의가
 * 무효로 다퉈질 수 있다. 세부 항목은 "자세히 보기"로 접는다.
 */
export default function BrochureGate({ onUnlocked }: { onUnlocked: (token: string) => void }) {
  const [values, setValues] = useState<Values>(EMPTY)
  const [errors, setErrors] = useState<Errors>({})
  const [sending, setSending] = useState(false)
  const attribution = useRef<Attribution | null>(null)
  const started = useRef(false)
  const viewed = useRef(false)

  useEffect(() => {
    attribution.current = ensureAttributionCaptured()
    if (viewed.current) return
    viewed.current = true
    trackEvent('lead_form_view', { form: 'brochure', ...attributionEventParams(attribution.current) })
  }, [])

  function update<K extends keyof Values>(key: K, value: Values[K]) {
    if (!started.current) {
      started.current = true
      trackEvent('lead_form_start', { form: 'brochure', ...attributionEventParams(attribution.current) })
    }
    setValues((prev) => ({ ...prev, [key]: value }))
    setErrors((prev) => ({ ...prev, [key]: undefined, form: undefined }))
  }

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const found = validate(values)
    setErrors(found)
    if (Object.keys(found).length > 0) return

    setSending(true)
    try {
      const response = await fetch('/api/brochure/leads', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({
          hospitalName: values.hospitalName.trim(),
          directorName: values.directorName.trim(),
          phone: values.phone.trim(),
          consent: true,
          sourcePath: '/brochure',
          attribution: attribution.current ? serializeAttribution(attribution.current) : '',
          website: (event.currentTarget.elements.namedItem('website') as HTMLInputElement | null)?.value ?? '',
        }),
      })
      const data = (await response.json().catch(() => ({}))) as { ok?: boolean; token?: string; error?: string }
      if (!response.ok || !data.ok || !data.token) {
        setErrors({ form: data.error || '잠시 후 다시 시도해 주세요.' })
        return
      }
      // 도입문의 전환(`generate_lead`)과 섞이지 않게 폼 이름으로 가른다.
      trackEvent('brochure_lead', { form: 'brochure', ...attributionEventParams(attribution.current) })
      onUnlocked(data.token)
    } catch {
      setErrors({ form: '접수 서버에 연결하지 못했습니다. 잠시 후 다시 시도해 주세요.' })
    } finally {
      setSending(false)
    }
  }

  return (
    <form className="brochure-gate" onSubmit={submit} noValidate>
      <p className="brochure-gate-title">세 가지만 입력하시면 바로 열립니다</p>

      <div className="brochure-field">
        <label htmlFor="brochure-hospital">병원명</label>
        <input
          id="brochure-hospital"
          name="hospitalName"
          autoComplete="organization"
          placeholder="OOO정형외과"
          value={values.hospitalName}
          onChange={(e) => update('hospitalName', e.target.value)}
          aria-invalid={errors.hospitalName ? 'true' : undefined}
          aria-describedby={errors.hospitalName ? 'brochure-hospital-error' : undefined}
        />
        {errors.hospitalName && <p id="brochure-hospital-error" className="brochure-error">{errors.hospitalName}</p>}
      </div>

      <div className="brochure-field">
        <label htmlFor="brochure-director">원장님 성함</label>
        <input
          id="brochure-director"
          name="directorName"
          autoComplete="name"
          value={values.directorName}
          onChange={(e) => update('directorName', e.target.value)}
          aria-invalid={errors.directorName ? 'true' : undefined}
          aria-describedby={errors.directorName ? 'brochure-director-error' : undefined}
        />
        {errors.directorName && <p id="brochure-director-error" className="brochure-error">{errors.directorName}</p>}
      </div>

      <div className="brochure-field">
        <label htmlFor="brochure-phone">휴대폰 번호</label>
        <input
          id="brochure-phone"
          name="phone"
          type="tel"
          inputMode="tel"
          autoComplete="tel"
          placeholder="010-0000-0000"
          value={values.phone}
          onChange={(e) => update('phone', e.target.value)}
          aria-invalid={errors.phone ? 'true' : undefined}
          aria-describedby={errors.phone ? 'brochure-phone-error' : undefined}
        />
        {errors.phone && <p id="brochure-phone-error" className="brochure-error">{errors.phone}</p>}
      </div>

      <div className="brochure-consent">
        <label>
          <input
            type="checkbox"
            checked={values.consent}
            onChange={(e) => update('consent', e.target.checked)}
            aria-invalid={errors.consent ? 'true' : undefined}
          />
          <span>
            (필수) 개인정보 수집·이용과 소개서 열람 기록(페이지별 열람 시간) 활용에 동의합니다.
          </span>
        </label>
        <details>
          <summary>자세히 보기</summary>
          <ul>
            <li>수집 항목: 병원명, 원장님 성함, 휴대폰 번호, 소개서 열람 기록(열람 일시, 페이지별 열람 시간, 기기 구분값)</li>
            <li>이용 목적: 소개서 제공, 열람 내용에 맞춘 도입 상담 안내</li>
            <li>보유 기간: 수집일로부터 180일 이내 파기</li>
            <li>동의를 거부하실 수 있으며, 거부 시 소개서 열람만 제한됩니다.</li>
          </ul>
          <Link href="/privacy">개인정보 처리방침 전문</Link>
        </details>
        {errors.consent && <p className="brochure-error">{errors.consent}</p>}
      </div>

      {/* honeypot — 사람에게는 보이지 않는다 */}
      <div className="hp-field" aria-hidden="true">
        <label htmlFor="brochure-website">웹사이트</label>
        <input id="brochure-website" name="website" tabIndex={-1} autoComplete="off" />
      </div>

      {errors.form && <p className="brochure-error brochure-error-block" role="alert">{errors.form}</p>}

      <button className="btn btn-primary btn-lg brochure-submit" type="submit" disabled={sending}>
        {sending ? '여는 중…' : '소개서 바로 보기'}
      </button>
    </form>
  )
}
