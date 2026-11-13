import { CLINIC_IMAGE_SIZES } from '@/lib/clinic-image-delivery'
import {
  physicianCareerNeedsDisclosure,
  physicianDisplayChips,
  physicianEyebrow,
  physicianNameSuffix,
  physicianRoleLabel,
  type ClinicPhysician,
} from '@/lib/clinic-physicians'

import { ClinicAvatar } from '../_components/ClinicAvatar'

interface Props {
  physician: ClinicPhysician
  /** 의료진이 한 명일 때만 병원 지역 칩을 섞는다(여러 명이면 사람마다 같은 칩이 반복된다). */
  region: string[]
  className: string
  photoClassName: string
  bodyClassName: string
  priority?: boolean
  /** C안처럼 자격·경력을 표로 보일 때 true. */
  asTable?: boolean
  tableClassName?: string
}

/**
 * 레이아웃 A·C의 의료진 한 사람. 레이아웃 도입 전 화면(`DoctorIntro`)과 같은 사실·같은
 * 표기 규칙을 쓴다 — 스키마의 의료진 전원이 화면에도 나오고, 생성 인물은 쓰지 않는다
 * (사진이 없으면 이름 첫 글자).
 */
export function PhysicianProfile({
  physician,
  region,
  className,
  photoClassName,
  bodyClassName,
  priority = false,
  asTable = false,
  tableClassName,
}: Props) {
  const chips = physicianDisplayChips(physician, region)
  const eyebrow = physicianEyebrow(physician)
  const role = physicianRoleLabel(physician)
  const suffix = physicianNameSuffix(physician)
  const boardCerts = (physician.credentials?.board_certifications ?? []).filter(Boolean)
  const societies = (physician.credentials?.society_memberships ?? []).filter(Boolean)
  const school = physician.credentials?.medical_school?.trim() || null
  const career = physician.career
  const careerNode = career ? (
    physicianCareerNeedsDisclosure(career) ? (
      <details>
        <summary>약력 더보기</summary>
        <p style={{ whiteSpace: 'pre-line' }}>{career}</p>
      </details>
    ) : (
      <p style={{ whiteSpace: 'pre-line' }}>{career}</p>
    )
  ) : null

  return (
    <article className={className}>
      <ClinicAvatar
        src={physician.photoUrl}
        alt={`${physician.name}${suffix ? ` ${suffix}` : ''}`}
        wrapperClassName={photoClassName}
        sizes={CLINIC_IMAGE_SIZES.portraitSolo}
        priority={priority}
        fallback={<span key="monogram" aria-hidden="true">{physician.name.slice(0, 1) || '醫'}</span>}
      />
      <div className={bodyClassName}>
        {eyebrow ? <p data-role="eyebrow">{eyebrow}</p> : null}
        {/* `원장`은 텍스트 공백으로 띄운다 — 마진만 주면 추출된 문장이 `조재현원장`으로 붙는다. */}
        <h3>
          {physician.name}
          {suffix ? <small>{` ${suffix}`}</small> : null}
        </h3>
        {role ? <p data-role="role">{role}</p> : null}
        {chips.length > 0 ? (
          <ul data-role="chips">
            {chips.map((chip) => (
              <li key={chip}>{chip}</li>
            ))}
          </ul>
        ) : null}
        {asTable ? (
          <table className={tableClassName}>
            <tbody>
              {boardCerts.length > 0 ? (
                <tr><th scope="row">전문의</th><td>{boardCerts.join(' · ')}</td></tr>
              ) : null}
              {school ? (
                <tr><th scope="row">출신 학교</th><td>{school}</td></tr>
              ) : null}
              {careerNode ? (
                <tr><th scope="row">경력</th><td>{careerNode}</td></tr>
              ) : null}
              {societies.length > 0 ? (
                <tr><th scope="row">학회</th><td>{societies.join(' · ')}</td></tr>
              ) : null}
            </tbody>
          </table>
        ) : (
          <>
            {careerNode}
            {boardCerts.length > 0 ? <p data-role="credentials">{boardCerts.join(' · ')}</p> : null}
            {societies.length > 0 ? <p data-role="societies">{societies.join(' · ')}</p> : null}
          </>
        )}
      </div>
    </article>
  )
}
