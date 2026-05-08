import Link from 'next/link'

import { ChevronRightIcon, PhoneIcon } from './icons'

interface Props {
  hospitalName: string
  hospitalSlug: string
  region: string[]
  specialties: string[]
  phone: string
}

export function ClinicHero({ hospitalName, hospitalSlug, region, specialties, phone }: Props) {
  const eyebrowLabel = [region[0], specialties[0]].filter(Boolean).join(' · ')
  const meta = [region.join(' '), specialties.join(' · ')].filter(Boolean).join(' / ')

  return (
    <section className="clinic-hero" id="top">
      <div className="clinic-hero-inner">
        {eyebrowLabel && <span className="clinic-hero-eyebrow">{eyebrowLabel}</span>}
        <h1 className="clinic-hero-title">{hospitalName}</h1>
        {meta && (
          <p className="clinic-hero-meta">
            <strong>{meta}</strong> 환자 질문에 답하는 진료 정보를 정리했습니다.
          </p>
        )}
        <div className="clinic-hero-actions">
          <a className="clinic-btn clinic-btn-primary" href={`tel:${phone}`}>
            <PhoneIcon className="clinic-icon clinic-icon--sm" style={{ color: 'currentColor' }} />
            전화 예약 · {phone}
          </a>
          <Link className="clinic-btn clinic-btn-secondary" href={`/${hospitalSlug}/contents`}>
            의료 정보 모아 보기
            <ChevronRightIcon className="clinic-icon clinic-icon--sm" style={{ color: 'currentColor' }} />
          </Link>
        </div>
      </div>
    </section>
  )
}
