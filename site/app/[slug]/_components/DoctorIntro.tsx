import Image from 'next/image'

interface Props {
  directorName: string
  directorCareer: string
  directorPhotoUrl: string | null
  specialty: string | null
}

export function DoctorIntro({ directorName, directorCareer, directorPhotoUrl, specialty }: Props) {
  return (
    <section id="doctor" className="clinic-section clinic-section--alt">
      <div className="clinic-section-inner">
        <header className="clinic-section-header">
          <span className="clinic-section-eyebrow">Director</span>
          <h2 className="clinic-section-heading">원장 소개</h2>
          <p className="clinic-section-lede">
            진료 결정에서 가장 먼저 확인하시는 것이 의료진의 이력입니다. 약력은 병원에서 직접 확인한
            정보입니다.
          </p>
        </header>

        <div className="clinic-doctor">
          {directorPhotoUrl ? (
            <div className="clinic-doctor-photo">
              <Image
                src={directorPhotoUrl}
                alt={`${directorName} 원장 사진`}
                fill
                sizes="(max-width: 720px) 160px, 200px"
                style={{ objectFit: 'cover' }}
              />
            </div>
          ) : (
            <div className="clinic-doctor-photo clinic-doctor-photo--placeholder" aria-hidden="true">
              사진 준비중
            </div>
          )}

          <div>
            {specialty && <span className="clinic-doctor-tag">{specialty}</span>}
            <h3 className="clinic-doctor-name">
              {directorName}
              <small>원장</small>
            </h3>
            {directorCareer ? (
              <p className="clinic-doctor-career">{directorCareer}</p>
            ) : (
              <p className="clinic-doctor-career" style={{ color: 'var(--color-revisit-text-helper)' }}>
                약력 정보를 준비하고 있습니다.
              </p>
            )}
          </div>
        </div>
      </div>
    </section>
  )
}
