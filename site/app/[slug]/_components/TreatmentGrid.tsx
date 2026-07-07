import Link from 'next/link'

import { buildTreatmentSlug } from '@/lib/treatment-slug'

import { ChevronRightIcon } from './icons'

interface Treatment {
  name: string
  description: string
}

interface Props {
  treatments: Treatment[]
  hospitalSlug: string
}

export function TreatmentGrid({ treatments, hospitalSlug }: Props) {
  if (!treatments || treatments.length === 0) {
    return null
  }

  const renderRow = (treatment: Treatment, isLead = false) => {
    const treatmentSlug = buildTreatmentSlug(treatment.name)
    const href = treatmentSlug ? `/${hospitalSlug}/treatments/${treatmentSlug}` : null
    const className = `clinic-tx-row${isLead ? ' clinic-tx-row--lead' : ''}`
    const inner = (
      <>
        <span className="clinic-tx-term">{treatment.name}</span>
        <span className="clinic-tx-desc">
          {treatment.description || '진료 상담에서 자세한 내용을 확인해 주세요.'}
        </span>
        {href && <ChevronRightIcon className="clinic-icon clinic-icon--sm clinic-tx-arrow" aria-hidden="true" />}
      </>
    )
    return (
      <li key={treatment.name} className="clinic-tx-item">
        {href ? (
          <Link href={href} className={className}>
            {inner}
          </Link>
        ) : (
          <div className={className}>{inner}</div>
        )}
      </li>
    )
  }

  return (
    <section id="treatments" className="clinic-section">
      <div className="clinic-section-inner">
        <header className="clinic-section-head">
          <h2 className="clinic-section-title">진료 영역</h2>
          <p className="clinic-section-note">
            병원에서 주로 진료하는 영역입니다. 증상과 치료 방법은 개인마다 다를 수 있으니
            자세한 내용은 진료 상담에서 확인해 주세요.
          </p>
        </header>

        <ul className="clinic-tx-deflist" aria-label="진료 영역 목록">
          {renderRow(treatments[0], true)}
          {treatments.slice(1).map((treatment) => renderRow(treatment))}
        </ul>
      </div>
    </section>
  )
}
