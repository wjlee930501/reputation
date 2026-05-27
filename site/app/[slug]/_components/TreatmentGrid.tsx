import { pickIconForTreatment } from './MedicalIcons'

interface Treatment {
  name: string
  description: string
}

interface Props {
  treatments: Treatment[]
}

export function TreatmentGrid({ treatments }: Props) {
  if (!treatments || treatments.length === 0) {
    return null
  }

  return (
    <section id="treatments" className="clinic-section">
      <div className="clinic-section-inner">
        <header className="clinic-section-header">
          <span className="clinic-section-label">진료 안내</span>
          <h2 className="clinic-section-heading">진료 영역</h2>
          <p className="clinic-section-lede">
            병원에서 주로 진료하는 영역입니다. 증상과 치료 방법은 개인마다 다를 수 있으니
            자세한 내용은 진료 상담에서 확인해 주세요.
          </p>
        </header>

        <ul className="clinic-treatment-grid" aria-label="진료 영역 목록">
          {treatments.map((treatment) => {
            const { Icon, hue } = pickIconForTreatment(treatment.name)
            return (
              <li key={treatment.name} className="clinic-treatment-card">
                <span className={`clinic-treatment-card-icon hue-${hue}`} aria-hidden="true">
                  <Icon />
                </span>
                <span className="clinic-treatment-card-copy">
                  <span className="clinic-treatment-card-name">{treatment.name}</span>
                  {treatment.description && (
                    <span className="clinic-treatment-card-desc">{treatment.description}</span>
                  )}
                </span>
              </li>
            )
          })}
        </ul>
      </div>
    </section>
  )
}
