'use client'

import Link from 'next/link'
import { useCallback, useEffect, useRef, useState } from 'react'
import { usePathname } from 'next/navigation'
import { attributionEventParams, decorateSourcePath, type Attribution } from '@/lib/ad-attribution'
import { ensureAttributionCaptured } from '@/lib/ad-attribution-client'
import { trackEvent } from '@/lib/analytics'
import { diagnosisInputError, inquirySourcePath, isReasonableKoreanMobilePhone, isValidHttpUrl } from '@/lib/inquiry-lead'

type FormValues = {
  clinicName: string
  clinicAddress: string
  directorName: string
  directorPhone: string
  homepage: string
  specialty: string
  regionKeyword: string
  coreKeywords: string
  privacy: boolean
}

type FieldErrors = Partial<Record<keyof FormValues, string>>

type Submission =
  | { phase: 'idle' }
  | { phase: 'sending' }
  | { phase: 'done' }
  | { phase: 'error'; message: string }

const EMPTY: FormValues = {
  clinicName: '',
  clinicAddress: '',
  directorName: '',
  directorPhone: '',
  homepage: '',
  specialty: '',
  regionKeyword: '',
  coreKeywords: '',
  privacy: false,
}

function validate(values: FormValues): FieldErrors {
  const errors: FieldErrors = {}
  if (!values.clinicName.trim()) errors.clinicName = '병원명을 입력해 주세요.'
  if (!values.clinicAddress.trim()) errors.clinicAddress = '병원 주소를 입력해 주세요.'
  if (!values.directorName.trim()) errors.directorName = '원장님 성함을 입력해 주세요.'
  if (!values.directorPhone.trim()) {
    errors.directorPhone = '원장님 연락처를 입력해 주세요.'
  } else if (!isReasonableKoreanMobilePhone(values.directorPhone)) {
    errors.directorPhone = '휴대전화 번호 형식을 확인해 주세요.'
  }
  if (!values.homepage.trim()) {
    errors.homepage = '병원 홈페이지를 입력해 주세요.'
  } else if (!isValidHttpUrl(values.homepage)) {
    errors.homepage = 'http:// 또는 https://로 시작하는 주소를 입력해 주세요.'
  }
  if (!values.specialty.trim()) errors.specialty = '진료과를 입력해 주세요.'
  if (!values.regionKeyword.trim()) errors.regionKeyword = '지역 키워드를 입력해 주세요.'
  if (!values.coreKeywords.trim()) {
    errors.coreKeywords = '핵심 진료 항목을 1개 이상 입력해 주세요.'
  } else if (values.specialty.trim() && values.regionKeyword.trim()) {
    const problem = diagnosisInputError(values)
    if (problem === 'empty') errors.coreKeywords = '핵심 진료 항목을 1개 이상 입력해 주세요.'
    if (problem === 'contains-clinic-name') {
      errors.coreKeywords = '진료과·지역·진료 항목에는 병원명을 넣을 수 없습니다. 진료 항목이나 증상을 입력해 주세요.'
    }
  }
  if (!values.privacy) errors.privacy = '개인정보 수집·이용에 동의해 주세요.'
  return errors
}

/**
 * 도입문의 폼 — 전용 필드를 `/api/leads`로 보내고, 업스트림의 기존
 * `contact`/`question` 매핑은 신뢰 경계인 서버에서 구성한다.
 *
 * 진료과·지역·키워드는 접수 직후 백엔드가 만드는 초도 노출 진단의 질의 재료다.
 * 안내 문자가 "현황 분석 자료를 준비해 설명드리겠다"고 약속하므로 필수로 받는다.
 */
export default function ContactForm() {
  const pathname = usePathname()
  const [values, setValues] = useState<FormValues>(EMPTY)
  const [errors, setErrors] = useState<FieldErrors>({})
  const [submission, setSubmission] = useState<Submission>({ phase: 'idle' })
  // 도입문의도 같은 리드 테이블로 들어간다 — 광고 유입 여부는 여기서도 남아야 한다.
  const attribution = useRef<Attribution | null>(null)
  const started = useRef(false)
  const viewed = useRef(false)

  useEffect(() => {
    attribution.current = ensureAttributionCaptured()
    // StrictMode(개발)와 리마운트에서 이펙트가 두 번 돈다 — 조회수가 두 배로 잡히면
    // 폼 전환율이 절반으로 보인다.
    if (viewed.current) return
    viewed.current = true
    trackEvent('lead_form_view', { form: 'inquiry', ...attributionEventParams(attribution.current) })
  }, [])

  const update = useCallback((name: keyof FormValues, value: string | boolean) => {
    if (!started.current) {
      started.current = true
      trackEvent('lead_form_start', {
        form: 'inquiry',
        ...attributionEventParams(attribution.current),
      })
    }
    setValues((prev) => ({ ...prev, [name]: value }))
    setErrors((prev) => ({ ...prev, [name]: undefined }))
  }, [])

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault()
    const found = validate(values)
    setErrors(found)
    if (Object.keys(found).length > 0) return

    setSubmission({ phase: 'sending' })
    try {
      const body = new FormData()
      body.set('clinicName', values.clinicName.trim())
      body.set('clinicAddress', values.clinicAddress.trim())
      body.set('directorName', values.directorName.trim())
      body.set('directorPhone', values.directorPhone.trim())
      body.set('homepage', values.homepage.trim())
      body.set('specialty', values.specialty.trim())
      body.set('regionKeyword', values.regionKeyword.trim())
      body.set('coreKeywords', values.coreKeywords.trim())
      body.set('privacy', 'on')
      body.set('consent_version', 'v1.2026-08')
      body.set('source_path', decorateSourcePath(inquirySourcePath(pathname), attribution.current))
      // honeypot — leave empty
      body.set('website', '')

      const response = await fetch('/api/leads', {
        method: 'POST',
        headers: { Accept: 'application/json' },
        body,
      })
      const data = await response.json().catch(() => ({}))
      if (!response.ok) {
        setSubmission({
          phase: 'error',
          message: (data as { error?: string })?.error || '접수에 실패했습니다. 잠시 후 다시 시도해 주세요.',
        })
        return
      }
      trackEvent('generate_lead', {
        form: 'inquiry',
        ...attributionEventParams(attribution.current),
      })
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
      <div className="inquiry-panel inquiry-done" role="status">
        <h3>문의가 접수되었습니다</h3>
        <p>
          남겨 주신 연락처로 안내 문자를 보내드리고, 입력하신 진료과·지역·키워드로 병원의
          현재 AI 노출 현황을 정리해 상담 때 함께 보여드립니다.
          영업 목적의 반복 연락 없이, 문의에 답하는 용도로만 사용합니다.
        </p>
      </div>
    )
  }

  return (
    <div className="inquiry-panel">
      <form className="inquiry-form" onSubmit={handleSubmit} noValidate>
        <div className="inquiry-field">
          <label htmlFor="inquiry-clinicName">병원명</label>
          <input
            id="inquiry-clinicName"
            name="clinicName"
            type="text"
            value={values.clinicName}
            placeholder="OOO정형외과"
            autoComplete="organization"
            required
            onChange={(e) => update('clinicName', e.target.value)}
            aria-invalid={Boolean(errors.clinicName)}
            disabled={submission.phase === 'sending'}
          />
          {errors.clinicName && <p className="inquiry-error">{errors.clinicName}</p>}
        </div>

        <div className="inquiry-field">
          <label htmlFor="inquiry-clinicAddress">병원 주소</label>
          <input
            id="inquiry-clinicAddress"
            name="clinicAddress"
            type="text"
            value={values.clinicAddress}
            placeholder="서울 강남구 테헤란로 00"
            autoComplete="street-address"
            required
            onChange={(e) => update('clinicAddress', e.target.value)}
            aria-invalid={Boolean(errors.clinicAddress)}
            disabled={submission.phase === 'sending'}
          />
          {errors.clinicAddress && <p className="inquiry-error">{errors.clinicAddress}</p>}
        </div>

        <div className="inquiry-field">
          <label htmlFor="inquiry-directorName">원장님 성함</label>
          <input
            id="inquiry-directorName"
            name="directorName"
            type="text"
            value={values.directorName}
            autoComplete="name"
            required
            onChange={(e) => update('directorName', e.target.value)}
            aria-invalid={Boolean(errors.directorName)}
            disabled={submission.phase === 'sending'}
          />
          {errors.directorName && <p className="inquiry-error">{errors.directorName}</p>}
        </div>

        <div className="inquiry-field">
          <label htmlFor="inquiry-directorPhone">원장님 연락처</label>
          <input
            id="inquiry-directorPhone"
            name="directorPhone"
            type="tel"
            inputMode="tel"
            value={values.directorPhone}
            placeholder="010-0000-0000"
            autoComplete="tel"
            required
            onChange={(e) => update('directorPhone', e.target.value)}
            aria-invalid={Boolean(errors.directorPhone)}
            disabled={submission.phase === 'sending'}
          />
          {errors.directorPhone && <p className="inquiry-error">{errors.directorPhone}</p>}
        </div>

        <div className="inquiry-field">
          <label htmlFor="inquiry-homepage">병원 홈페이지</label>
          <input
            id="inquiry-homepage"
            name="homepage"
            type="url"
            value={values.homepage}
            placeholder="https://www.example.com"
            autoComplete="url"
            required
            onChange={(e) => update('homepage', e.target.value)}
            aria-invalid={Boolean(errors.homepage)}
            disabled={submission.phase === 'sending'}
          />
          {errors.homepage && <p className="inquiry-error">{errors.homepage}</p>}
        </div>

        <div className="inquiry-field">
          <label htmlFor="inquiry-specialty">진료과</label>
          <input
            id="inquiry-specialty"
            name="specialty"
            type="text"
            value={values.specialty}
            placeholder="정형외과"
            required
            onChange={(e) => update('specialty', e.target.value)}
            aria-invalid={Boolean(errors.specialty)}
            disabled={submission.phase === 'sending'}
          />
          {errors.specialty && <p className="inquiry-error">{errors.specialty}</p>}
        </div>

        <div className="inquiry-field">
          <label htmlFor="inquiry-regionKeyword">지역 키워드</label>
          <input
            id="inquiry-regionKeyword"
            name="regionKeyword"
            type="text"
            value={values.regionKeyword}
            placeholder="대구 OO동, 서울 OO동"
            required
            onChange={(e) => update('regionKeyword', e.target.value)}
            aria-invalid={Boolean(errors.regionKeyword)}
            disabled={submission.phase === 'sending'}
          />
          {errors.regionKeyword && <p className="inquiry-error">{errors.regionKeyword}</p>}
        </div>

        <div className="inquiry-field">
          <label htmlFor="inquiry-coreKeywords">핵심 진료 항목 (쉼표로 구분, 최대 4개)</label>
          <input
            id="inquiry-coreKeywords"
            name="coreKeywords"
            type="text"
            value={values.coreKeywords}
            placeholder="건강검진, 신경차단술, 내성발톱"
            required
            onChange={(e) => update('coreKeywords', e.target.value)}
            aria-invalid={Boolean(errors.coreKeywords)}
            aria-describedby="inquiry-coreKeywords-hint"
            disabled={submission.phase === 'sending'}
          />
          <p id="inquiry-coreKeywords-hint" className="inquiry-hint">
            환자가 AI에 물어볼 만한 진료 항목이나 증상입니다. 병원명은 넣지 마세요. 접수 직후 이 조건으로
            병원의 현재 AI 노출 현황을 확인해 상담 때 함께 보여드립니다.
          </p>
          {errors.coreKeywords && <p className="inquiry-error">{errors.coreKeywords}</p>}
        </div>

        <div className="inquiry-field inquiry-consent">
          <label>
            <input
              type="checkbox"
              checked={values.privacy}
              onChange={(e) => update('privacy', e.target.checked)}
              disabled={submission.phase === 'sending'}
            />
            <span>
              개인정보 수집·이용에 동의합니다. (<Link href="/privacy">처리방침</Link>)
            </span>
          </label>
          {errors.privacy && <p className="inquiry-error">{errors.privacy}</p>}
        </div>

        {submission.phase === 'error' && (
          <p className="inquiry-error inquiry-error-block">{submission.message}</p>
        )}

        <button className="btn btn-primary btn-lg inquiry-submit" type="submit" disabled={submission.phase === 'sending'}>
          {submission.phase === 'sending' ? '접수 중…' : '도입 문의하기'}
        </button>
      </form>
    </div>
  )
}
