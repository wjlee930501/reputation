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
  region: string[]
  priorityPhoto?: boolean
}

/**
 * 사람마다 카드 하나. 배치는 인원수에서 온다.
 *
 * 카드 아래에 있던 `의료 정보 글 / 담당 진료 / 진료 지역` 띠는 지웠다 — 담당 진료와
 * 지역은 첫 화면 kicker가 이미 말하고, 글 수는 글이 없는 병원에서 `0편`을 크게
 * 적는 결과가 됐다. 글 수는 글 섹션의 "전체 보기" 링크가 맡는다.
 */
export function DoctorIntro({ physicians, region, priorityPhoto = false }: Props) {
  if (physicians.length === 0) return null
  const layout = clinicPhysicianLayout(physicians.length)

  return (
    <section id="doctor" className="hub-section">
      <div className="hub-container">
        <header className="hub-section-head">
          <h2 className="hub-section-title">진료를 담당하는 의료진</h2>
        </header>

        <div className={`hub-doctors hub-doctors--${layout}`}>
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
    <article className="hub-card hub-doctor">
      <ClinicAvatar
        src={physician.photoUrl}
        alt={`${physician.name}${suffix ? ` ${suffix}` : ''}`}
        wrapperClassName="hub-doctor-portrait"
        sizes={layout === 'solo' ? '(max-width: 719px) 100vw, 320px' : '(max-width: 719px) 100vw, (max-width: 1023px) 50vw, 380px'}
        priority={priorityPhoto}
        fallback={<span className="hub-doctor-monogram" aria-hidden="true">{initial}</span>}
      />

      <div className="hub-doctor-body">
        <div className="hub-doctor-identity">
          {eyebrow ? <span className="hub-eyebrow">{eyebrow}</span> : null}
          {/* `원장`은 마진이 아니라 **텍스트 공백**으로 띄운다. 마진만 주면 화면에서는
              떨어져 보여도 추출된 문장은 `전상훈원장`으로 붙는다. */}
          <h3 className="hub-doctor-name">
            {physician.name}
            {suffix ? <small>{` ${suffix}`}</small> : null}
          </h3>
          {role ? <span className="hub-doctor-role">{role}</span> : null}
        </div>

        {chips.length > 0 ? (
          <div className="hub-chip-row">
            {chips.map((chip) => (
              <span key={chip} className="hub-chip">{chip}</span>
            ))}
          </div>
        ) : null}

        {career ? (
          clamped ? (
            <details>
              <summary className="hub-doctor-career-toggle">약력 더보기</summary>
              <p className="hub-doctor-career">{career}</p>
            </details>
          ) : (
            <p className="hub-doctor-career">{career}</p>
          )
        ) : null}

        {boardCerts.length > 0 || societies.length > 0 ? (
          <div className="hub-doctor-creds">
            {boardCerts.length > 0 ? (
              <div>
                <span className="hub-cred-label">전문의 자격</span>
                <ul className="hub-cred-list">
                  {boardCerts.map((cert) => (
                    <li key={cert}>{cert}</li>
                  ))}
                </ul>
              </div>
            ) : null}
            {societies.length > 0 ? (
              <div>
                <span className="hub-cred-label">학회 활동</span>
                <ul className="hub-cred-list">
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
