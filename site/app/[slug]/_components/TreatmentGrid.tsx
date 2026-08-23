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
    <section id="treatments" className="clinic-section clinic-treatment-directory">
      <div className="clinic-section-inner">
        {/* P-A-3 — 이 제목은 sr-only였다. 다른 섹션은 모두 제목이 보이는데 첫
            섹션만 보이지 않아, 화면에서는 카드 네 장이 맥락 없이 시작됐다. */}
        <header className="clinic-section-head clinic-treatment-directory-head">
          <h2 className="clinic-section-title">진료 영역</h2>
          <p className="clinic-section-note">
            병원에서 주로 진료하는 영역입니다. 증상과 치료 방법은 개인마다 다를 수 있으니
            자세한 내용은 진료 상담에서 확인해 주세요.
          </p>
        </header>

        {/* 열 수를 항목 수에 맞춘다 — 4열로 고정하면 진료 항목이 1~3개인 병원에서
            테두리만 남은 빈 칸이 그려진다(P-C-1). */}
        <div
          className="clinic-tx-cards clinic-tx-directory"
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

        {treatments.length > lead.length ? (
          <Link href={`${hospitalRootUrl}/treatments`} className="clinic-tx-directory-more">
            진료 영역 전체 {countLabel(treatments.length, '개')} 보기
            <ChevronRightIcon className="clinic-icon clinic-icon--sm" style={{ color: 'currentColor' }} />
          </Link>
        ) : null}
      </div>
    </section>
  )
}
