import { CLINIC_IMAGE_SIZES } from '@/lib/clinic-image-delivery'
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
import { SectionLabel } from './SectionLabel'

import { ClinicAvatar } from './ClinicAvatar'
import { DOCTOR_SECTION_TITLE } from '@/lib/clinic-template-copy'

interface Props {
  /** 영문 소제목(섹션 설정·레이아웃 기본, `plan.label`). 없으면 그리지 않는다 — 지금 마크업 그대로. */
  sectionLabel?: string | null
  /** display_order 순서로 정규화된 의료진. 비어 있으면 섹션을 그리지 않는다. */
  physicians: ClinicPhysician[]
  specialties: string[]
  region: string[]
  contentCount: number
  priorityPhoto?: boolean
  /** 섹션 설정이 바꾼 제목·안내 문장(레이아웃 편집기). 없으면 지금 문구. */
  title?: string | null
  note?: string | null
  /** 섹션 설정이 바꾼 사진 비율·초점(`physicianPhotoLook`). null이면 지금 사진 모양. */
  photoLook?: PhysicianPhotoLook | null
  /** 직함을 이름 아래에 둔다(섹션 설정 `doctor.title_position = below`). 기본은 이름 위. */
  titleBelow?: boolean
}

type PhysicianPhotoLook = { aspectRatio: string | null; objectPosition: string | null }

export function DoctorIntro({
  physicians,
  specialties,
  region,
  contentCount,
  priorityPhoto = false,
  title,
  note,
  photoLook = null,
  titleBelow = false,
  sectionLabel = null,
}: Props) {
  if (physicians.length === 0) return null
  const layout = clinicPhysicianLayout(physicians.length)

  return (
    <section id="curator" className="clinic-section clinic-section--alt">
      <div className="clinic-section-inner">
        <header className="clinic-section-head">
          {sectionLabel ? <SectionLabel text={sectionLabel} /> : null}
          <h2 className="clinic-section-title">{title ?? DOCTOR_SECTION_TITLE}</h2>
          {note ? <p className="clinic-section-note">{note}</p> : null}
        </header>

        <div className={`clinic-curator-grid clinic-curator-grid--${layout}`}>
          {physicians.map((physician, index) => (
            <PhysicianCard
              key={physician.id}
              physician={physician}
              region={region}
              layout={layout}
              priorityPhoto={priorityPhoto && index === 0}
              photoLook={photoLook}
              titleBelow={titleBelow}
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
  photoLook,
  titleBelow,
}: {
  physician: ClinicPhysician
  region: string[]
  layout: 'solo' | 'pair' | 'grid'
  priorityPhoto: boolean
  photoLook: PhysicianPhotoLook | null
  titleBelow: boolean
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
          sizes={layout === 'solo' ? CLINIC_IMAGE_SIZES.portraitSolo : CLINIC_IMAGE_SIZES.portraitMultiple}
          priority={priorityPhoto}
          fallback={<span className="clinic-curator-monogram-glyph" aria-hidden="true">{initial}</span>}
          look={photoLook}
        />
      </div>

      <div className="clinic-curator-body">
        <div className="clinic-curator-identity">
          {eyebrow && !titleBelow ? <span className="clinic-curator-eyebrow">{eyebrow}</span> : null}
          {/* `원장`은 마진이 아니라 **텍스트 공백**으로 띄운다. 마진만 주면 화면에서는
              떨어져 보여도 추출된 문장은 `전상훈원장`으로 붙는다. */}
          <h3 className="clinic-curator-name">
            {physician.name}
            {suffix ? <small>{` ${suffix}`}</small> : null}
          </h3>
          {eyebrow && titleBelow ? (
            <span className="clinic-curator-eyebrow clinic-curator-eyebrow--below">{eyebrow}</span>
          ) : null}
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
