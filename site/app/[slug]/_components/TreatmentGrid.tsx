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

  // 홈페이지는 환자가 가장 먼저 확인할 대표 4개만 보여준다. 전체 항목은 상단의
  // '진료 영역' 페이지에서 탐색할 수 있어 첫 화면의 정보 밀도를 낮춘다.
  const lead = treatments.slice(0, 4)

  const hrefFor = (name: string): string | null => {
    const slug = buildTreatmentSlug(name)
    return slug ? `/${hospitalSlug}/treatments/${slug}` : null
  }

  return (
    <section id="treatments" className="clinic-section clinic-treatment-directory">
      <div className="clinic-section-inner">
        <header className="sr-only">
          <h2 className="clinic-section-title">진료 영역</h2>
          <p className="clinic-section-note">
            병원에서 주로 진료하는 영역입니다. 증상과 치료 방법은 개인마다 다를 수 있으니
            자세한 내용은 진료 상담에서 확인해 주세요.
          </p>
        </header>

        <div className="clinic-tx-cards clinic-tx-directory" aria-label="대표 진료 영역">
          {lead.map((treatment, idx) => {
            const href = hrefFor(treatment.name)
            const inner = (
              <>
                <span className="clinic-tx-card-index" aria-hidden="true">
                  {String(idx + 1).padStart(2, '0')}
                </span>
                <span className="clinic-tx-card-name">{treatment.name}</span>
                <span className="clinic-tx-card-desc clinic-tx-card-desc--supporting">
                  {treatment.description || '진료 상담에서 자세한 내용을 확인해 주세요.'}
                </span>
                {href && (
                  <span className="clinic-tx-card-more">
                    <ChevronRightIcon className="clinic-icon clinic-icon--sm" style={{ color: 'currentColor' }} />
                    <span className="sr-only">안내 보기</span>
                  </span>
                )}
              </>
            )
            return href ? (
              <Link key={treatment.name} href={href} className="clinic-tx-card">
                {inner}
              </Link>
            ) : (
              <div key={treatment.name} className="clinic-tx-card clinic-tx-card--static">
                {inner}
              </div>
            )
          })}
        </div>

      </div>
    </section>
  )
}
