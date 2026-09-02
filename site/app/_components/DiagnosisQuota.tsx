'use client'

import { useEffect, useState } from 'react'

import { diagnosisSlotResetCopy, parseDiagnosisSlots, type DiagnosisSlots } from '@/lib/diagnosis-slots'

type DiagnosisQuotaProps = {
  slots: DiagnosisSlots | null
  variant?: 'hero' | 'form' | 'cta'
}

export function useDiagnosisSlots(): DiagnosisSlots | null {
  const [slots, setSlots] = useState<DiagnosisSlots | null>(null)

  useEffect(() => {
    const controller = new AbortController()

    fetch('/api/diagnosis/slots', {
      cache: 'no-store',
      signal: controller.signal,
    })
      .then((response) => (response.ok ? response.json() : null))
      .then((value) => setSlots(parseDiagnosisSlots(value)))
      .catch(() => undefined)

    return () => controller.abort()
  }, [])

  return slots
}

export function DiagnosisQuota({ slots, variant = 'hero' }: DiagnosisQuotaProps) {
  return (
    <div className={`diagnosis-quota diagnosis-quota-${variant}`} aria-live="polite">
      <div className="diagnosis-quota-head">
        <p>매일 선착순 무료 진단</p>
        {slots ? (
          <p className="diagnosis-quota-count">
            <strong><b>{slots.used}</b> / {slots.total}개소</strong>
            <span>{slots.soldOut ? '오늘 접수 마감' : `오늘 신청 · ${slots.remaining}개소 남음`}</span>
          </p>
        ) : (
          <p className="diagnosis-quota-count">
            <strong>접수 현황</strong>
            <span>실시간 확인 중</span>
          </p>
        )}
      </div>
      <p className="diagnosis-quota-reset">{diagnosisSlotResetCopy()}</p>
    </div>
  )
}

export default function LiveDiagnosisQuota({ variant = 'hero' }: Pick<DiagnosisQuotaProps, 'variant'>) {
  const slots = useDiagnosisSlots()
  return <DiagnosisQuota slots={slots} variant={variant} />
}
