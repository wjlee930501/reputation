import Link from 'next/link'
import type { CSSProperties } from 'react'

import { countLabel } from '@/lib/clinic-counters'
import { buildTreatmentSlug } from '@/lib/treatment-slug'

import { ChevronRightIcon } from './icons'

interface Treatment {
  name: string
  description: string
}

interface Props {
  treatments: Treatment[]
  hospitalRootUrl: string
}

/** 홈에서 먼저 보여줄 대표 진료 영역 수. 열 수도 이 값 안에서 결정된다. */
const LEAD_LIMIT = 4

export function TreatmentGrid({ treatments, hospitalRootUrl }: Props) {
  if (!treatments || treatments.length === 0) {
    return null
  }

  // 홈페이지는 환자가 가장 먼저 확인할 대표 4개만 보여준다. 전체 항목은 아래
  // '전체 N개 보기' 링크로 이어진다.
  const lead = treatments.slice(0, LEAD_LIMIT)

  const hrefFor = (name: string): string | null => {
    const slug = buildTreatmentSlug(name)
    return slug ? `${hospitalRootUrl}/treatments/${slug}` : null
  }

  return (
    <section id="treatments" className="hub-section">
      <div className="hub-container">
        <header className="hub-section-head">
          <h2 className="hub-section-title">진료 영역</h2>
          <p className="hub-section-note">
            병원에서 주로 진료하는 영역입니다. 증상과 치료 방법은 개인마다 다를 수 있으니
            자세한 내용은 진료 상담에서 확인해 주세요.
          </p>
        </header>

        {/* 열 수를 항목 수에 맞춘다 — 4열로 고정하면 진료 항목이 1~3개인 병원에서
            테두리만 남은 빈 칸이 그려진다(P-C-1). */}
        <div
          className="hub-tx-grid"
          style={{
            '--clinic-tx-columns': Math.min(lead.length, LEAD_LIMIT),
            '--clinic-tx-columns-md': Math.min(lead.length, 2),
          } as CSSProperties}
          aria-label="대표 진료 영역"
        >
          {lead.map((treatment, idx) => {
            const href = hrefFor(treatment.name)
            const inner = (
              <>
                <span className="hub-tx-index" aria-hidden="true">
                  {String(idx + 1).padStart(2, '0')}
                </span>
                <span className="hub-tx-name">{treatment.name}</span>
                <span className="hub-tx-desc">
                  {treatment.description || '진료 상담에서 자세한 내용을 확인해 주세요.'}
                </span>
                {href && (
                  <>
                    <ChevronRightIcon className="hub-tx-arrow" />
                    <span className="sr-only">안내 보기</span>
                  </>
                )}
              </>
            )
            return href ? (
              <Link key={treatment.name} href={href} className="hub-card hub-tx-card">
                {inner}
              </Link>
            ) : (
              <div key={treatment.name} className="hub-card hub-tx-card">
                {inner}
              </div>
            )
          })}
        </div>

        {treatments.length > lead.length ? (
          <Link href={`${hospitalRootUrl}/treatments`} className="hub-more">
            진료 영역 전체 {countLabel(treatments.length, '개')} 보기
            <ChevronRightIcon className="hub-icon hub-icon--sm" style={{ color: 'currentColor' }} />
          </Link>
        ) : null}
      </div>
    </section>
  )
}
