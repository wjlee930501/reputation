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

/**
 * 남은 자리만 보여준다.
 *
 * 앞 버전은 `0 / 20개소`처럼 **신청 수를 함께 세웠다.** 선착순 고지의 목적은 "지금
 * 신청하면 오늘 받는다"를 전하는 것인데, 신청 수를 앞세우면 이른 시간대에는 그 숫자가
 * 0으로 서서 아무도 신청하지 않은 화면이 된다. 원장이 읽어야 하는 값은 하나다 —
 * 오늘 몇 곳이 남았는가.
 *
 * 마감일 때만 문구가 바뀐다. 마감인데 자리가 있는 것처럼 보이는 것이 가장 나쁘다.
 */
export function DiagnosisQuota({ slots, variant = 'hero' }: DiagnosisQuotaProps) {
  return (
    <div className={`diagnosis-quota diagnosis-quota-${variant}`} aria-live="polite">
      <div className="diagnosis-quota-head">
        <p>매일 선착순 무료 진단</p>
        {slots ? (
          <p className="diagnosis-quota-count">
            {slots.soldOut ? (
              <strong>오늘 접수 마감</strong>
            ) : (
              <strong>
                오늘 <b>{slots.remaining}</b>곳 접수 가능
              </strong>
            )}
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
