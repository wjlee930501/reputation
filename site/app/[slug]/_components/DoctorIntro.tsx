import { resolveAssetUrl, type HospitalPhoto } from '@/lib/api'

import { ClinicAvatar } from './ClinicAvatar'

interface Props {
  directorName: string
  directorCareer: string
  directorPhotoUrl: string | null
  specialties: string[]
  region: string[]
  contentCount: number
  boardCertifications?: string[] | null
  societyMemberships?: string[] | null
  // 병원 공개 API photos[]에 source_type=PHOTO_DOCTOR(원장 실사 기반 일러스트)가 있으면
  // director_photo_url이 비어도 이 사진을 원장 프로필에 렌더한다. 로드 실패 시 모노그램 폴백.
  photos?: HospitalPhoto[]
}

function resolveDoctorPhoto(directorPhotoUrl: string | null, photos: HospitalPhoto[]): string | null {
  const direct = resolveAssetUrl(directorPhotoUrl)
  if (direct) return direct
  const doctorPhoto = photos.find((p) => p.source_type === 'PHOTO_DOCTOR')
  return resolveAssetUrl(doctorPhoto?.url ?? null)
}

export function DoctorIntro({
  directorName,
  directorCareer,
  directorPhotoUrl,
  specialties,
  region,
  contentCount,
  boardCertifications,
  societyMemberships,
  photos = [],
}: Props) {
  const boardCerts = (boardCertifications ?? []).filter(Boolean)
  const societies = (societyMemberships ?? []).filter(Boolean)
  const resolvedPhoto = resolveDoctorPhoto(directorPhotoUrl, photos)
  const initial = (directorName || '').trim().slice(0, 1) || '醫'
  const doctorRole = boardCerts[0] ?? '대표원장'

  return (
    <section id="curator" className="clinic-section clinic-section--alt">
      <div className="clinic-section-inner">
        <header className="clinic-section-head">
          <h2 className="clinic-section-title">진료를 담당하는 의료진</h2>
          <p className="clinic-section-note">
            진료 경험과 환자 상담에서 반복되는 질문을 바탕으로, 필요한 의료 정보를 차분히 정리합니다.
          </p>
        </header>

        <div className="clinic-curator">
          <div className="clinic-curator-figure">
            <ClinicAvatar
              src={resolvedPhoto}
              alt={`${directorName} 원장`}
              wrapperClassName="clinic-curator-portrait"
              fallbackClassName="clinic-curator-portrait--monogram"
              fallback={
                <>
                  <span className="clinic-curator-monogram-glyph" aria-hidden="true">{initial}</span>
                  <span className="clinic-curator-monogram-name" aria-hidden="true">{directorName} 원장</span>
                </>
              }
            />
            <div className="clinic-curator-figure-meta">
              <span className="clinic-curator-eyebrow">대표원장</span>
              <h3 className="clinic-curator-name">
                {directorName}
                <small>원장</small>
              </h3>
              <span className="clinic-curator-role">{doctorRole}</span>
            </div>
          </div>

          <div className="clinic-curator-body">
            <div className="clinic-curator-tag-row">
              {specialties.map((s) => (
                <span key={s} className="clinic-curator-tag">{s}</span>
              ))}
              {region.map((r) => (
                <span key={`r-${r}`} className="clinic-curator-tag">{r}</span>
              ))}
            </div>

            {directorCareer ? (
              <p className="clinic-curator-career">{directorCareer}</p>
            ) : (
              <p className="clinic-curator-career" style={{ color: 'var(--color-revisit-text-helper)' }}>
                약력 정보를 준비하고 있습니다.
              </p>
            )}

            {boardCerts.length > 0 || societies.length > 0 ? (
              <div className="clinic-curator-credentials">
                {boardCerts.length > 0 ? (
                  <div className="clinic-curator-cred-group">
                    <span className="clinic-curator-cred-label">전문 자격</span>
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

            <div className="clinic-curator-meta">
              <div className="clinic-curator-meta-cell">
                <span className="clinic-curator-meta-label">의료 정보 글</span>
                <span className="clinic-curator-meta-value">{contentCount}편</span>
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
        </div>
      </div>
    </section>
  )
}
