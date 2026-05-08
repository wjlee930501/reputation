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
          <span className="clinic-section-eyebrow">Treatment Areas</span>
          <h2 className="clinic-section-heading">이 콘텐츠가 다루는 진료 영역</h2>
          <p className="clinic-section-lede">
            이 의료 콘텐츠 허브는 아래 진료 영역의 환자 질문에 집중합니다. 자세한 진단·치료
            안내는 검수된 발행 콘텐츠를 통해서만 제공합니다.
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
                <span className="clinic-treatment-card-name">{treatment.name}</span>
              </li>
            )
          })}
        </ul>
      </div>
    </section>
  )
}
