import { countLabel } from '@/lib/clinic-counters'
import {
  clinicPhysicianLayout,
  physicianCareerNeedsDisclosure,
  physicianDisplayChips,
  physicianEyebrow,
  physicianNameSuffix,
  physicianRoleLabel,
  type ClinicPhysician,
} from '@/lib/clinic-physicians'

import { ClinicAvatar } from './ClinicAvatar'

interface Props {
  /** display_order 순서로 정규화된 의료진. 비어 있으면 섹션을 그리지 않는다. */
  physicians: ClinicPhysician[]
  specialties: string[]
  region: string[]
  contentCount: number
  priorityPhoto?: boolean
}

export function DoctorIntro({
  physicians,
  specialties,
  region,
  contentCount,
  priorityPhoto = false,
}: Props) {
  if (physicians.length === 0) return null
  const layout = clinicPhysicianLayout(physicians.length)

  return (
    <section id="curator" className="clinic-section clinic-section--alt">
      <div className="clinic-section-inner">
        <header className="clinic-section-head">
          <h2 className="clinic-section-title">진료를 담당하는 의료진</h2>
        </header>

        <div className={`clinic-curator-grid clinic-curator-grid--${layout}`}>
          {physicians.map((physician, index) => (
            <PhysicianCard
              key={physician.id}
              physician={physician}
              region={region}
              layout={layout}
              priorityPhoto={priorityPhoto && index === 0}
            />
          ))}
        </div>

        <div className="clinic-curator-meta">
          <div className="clinic-curator-meta-cell">
            <span className="clinic-curator-meta-label">의료 정보 글</span>
            <span className="clinic-curator-meta-value">{countLabel(contentCount, '편')}</span>
          </div>
          <div className="clinic-curator-meta-cell">
            <span className="clinic-curator-meta-label">담당 진료</span>
            <span className="clinic-curator-meta-value">
              {specialties.length > 0 ? specialties.join(' · ') : '-'}
            </span>
          </div>
          <div className="clinic-curator-meta-cell">
            <span className="clinic-curator-meta-label">진료 지역</span>
            <span className="clinic-curator-meta-value">
              {region.length > 0 ? region.join(' · ') : '-'}
            </span>
          </div>
        </div>
      </div>
    </section>
  )
}

function PhysicianCard({
  physician,
  region,
  layout,
  priorityPhoto,
}: {
  physician: ClinicPhysician
  region: string[]
  layout: 'solo' | 'pair' | 'grid'
  priorityPhoto: boolean
}) {
  // 진료 지역은 병원 단위 사실이라 카드가 하나일 때만 인물 칩에 섞는다. 여러 명이면
  // 사람마다 같은 지역 칩이 반복되어 그 사람의 진료영역을 읽기 어려워진다.
  const chips = physicianDisplayChips(physician, layout === 'solo' ? region : [])
  const eyebrow = physicianEyebrow(physician)
  const role = physicianRoleLabel(physician)
  const suffix = physicianNameSuffix(physician)
  const initial = physician.name.slice(0, 1) || '醫'
  const boardCerts = (physician.credentials?.board_certifications ?? []).filter(Boolean)
  const societies = (physician.credentials?.society_memberships ?? []).filter(Boolean)
  const career = physician.career
  const clamped = physicianCareerNeedsDisclosure(career)

  return (
    <article className="clinic-curator">
      <div className="clinic-curator-figure">
        <ClinicAvatar
          src={physician.photoUrl}
          alt={`${physician.name}${suffix ? ` ${suffix}` : ''}`}
          wrapperClassName="clinic-curator-portrait"
          fallbackClassName="clinic-curator-portrait--monogram"
          sizes={layout === 'solo' ? '(max-width: 720px) 160px, 320px' : '(max-width: 720px) 140px, 260px'}
          priority={priorityPhoto}
          fallback={<span className="clinic-curator-monogram-glyph" aria-hidden="true">{initial}</span>}
        />
      </div>

      <div className="clinic-curator-body">
        <div className="clinic-curator-identity">
          {eyebrow ? <span className="clinic-curator-eyebrow">{eyebrow}</span> : null}
          {/* `원장`은 마진이 아니라 **텍스트 공백**으로 띄운다. 마진만 주면 화면에서는
              떨어져 보여도 추출된 문장은 `전상훈원장`으로 붙는다. */}
          <h3 className="clinic-curator-name">
            {physician.name}
            {suffix ? <small>{` ${suffix}`}</small> : null}
          </h3>
          {role ? <span className="clinic-curator-role">{role}</span> : null}
        </div>

        {chips.length > 0 ? (
          <div className="clinic-curator-tag-row">
            {chips.map((chip) => (
              <span key={chip} className="clinic-curator-tag">{chip}</span>
            ))}
          </div>
        ) : null}

        {career ? (
          clamped ? (
            <details className="clinic-curator-career-disclosure">
              <summary className="clinic-curator-career-summary">약력 더보기</summary>
              <p className="clinic-curator-career">{career}</p>
            </details>
          ) : (
            <p className="clinic-curator-career">{career}</p>
          )
        ) : null}

        {boardCerts.length > 0 || societies.length > 0 ? (
          <div className="clinic-curator-credentials">
            {boardCerts.length > 0 ? (
              <div className="clinic-curator-cred-group">
                <span className="clinic-curator-cred-label">전문의 자격</span>
                <div className="clinic-curator-cred-chips">
                  {boardCerts.map((cert) => (
                    <span key={cert} className="clinic-curator-cred-chip">{cert}</span>
                  ))}
                </div>
              </div>
            ) : null}
            {societies.length > 0 ? (
              <div className="clinic-curator-cred-group">
                <span className="clinic-curator-cred-label">학회 활동</span>
                <ul className="clinic-curator-cred-list">
                  {societies.map((society) => (
                    <li key={society}>{society}</li>
                  ))}
                </ul>
              </div>
            ) : null}
          </div>
        ) : null}
      </div>
    </article>
  )
}
