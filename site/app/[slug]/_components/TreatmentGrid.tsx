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

  // 대표 4개(배열 앞 4개)는 큰 카드, 나머지는 2열 컴팩트 인덱스 — 병원별 항목 수와 무관하게
  // 같은 위계 효과를 준다(하드코딩 분류 없이 데이터 독립적).
  const lead = treatments.slice(0, 4)
  const rest = treatments.slice(4)

  const hrefFor = (name: string): string | null => {
    const slug = buildTreatmentSlug(name)
    return slug ? `/${hospitalSlug}/treatments/${slug}` : null
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

        <div className="clinic-tx-cards" aria-label="대표 진료 영역">
          {lead.map((treatment, idx) => {
            const href = hrefFor(treatment.name)
            const inner = (
              <>
                <span className="clinic-tx-card-index" aria-hidden="true">
                  {String(idx + 1).padStart(2, '0')}
                </span>
                <span className="clinic-tx-card-name">{treatment.name}</span>
                <span className="clinic-tx-card-desc">
                  {treatment.description || '진료 상담에서 자세한 내용을 확인해 주세요.'}
                </span>
                {href && (
                  <span className="clinic-tx-card-more">
                    안내 보기
                    <ChevronRightIcon className="clinic-icon clinic-icon--sm" style={{ color: 'currentColor' }} />
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

        {rest.length > 0 && (
          <ul className="clinic-tx-index" aria-label="그 밖의 진료 영역">
            {rest.map((treatment) => {
              const href = hrefFor(treatment.name)
              const inner = (
                <>
                  <span className="clinic-tx-index-name">{treatment.name}</span>
                  {href && <ChevronRightIcon className="clinic-icon clinic-icon--sm clinic-tx-index-arrow" aria-hidden="true" />}
                </>
              )
              return (
                <li key={treatment.name}>
                  {href ? (
                    <Link href={href} className="clinic-tx-index-row">
                      {inner}
                    </Link>
                  ) : (
                    <div className="clinic-tx-index-row">{inner}</div>
                  )}
                </li>
              )
            })}
          </ul>
        )}
      </div>
    </section>
  )
}
