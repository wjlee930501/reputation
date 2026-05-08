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
          <span className="clinic-section-eyebrow">Treatments</span>
          <h2 className="clinic-section-heading">진료 분야</h2>
          <p className="clinic-section-lede">
            병원에서 정기적으로 진행하는 진료 항목입니다. 자세한 진단·치료 안내는 의료 정보 메뉴의
            발행된 콘텐츠를 통해서만 제공합니다.
          </p>
        </header>

        <ul className="clinic-treatment-grid" aria-label="진료 항목 목록">
          {treatments.map((treatment) => (
            <li key={treatment.name} className="clinic-treatment-card">
              <span className="clinic-treatment-card-name">{treatment.name}</span>
            </li>
          ))}
        </ul>
      </div>
    </section>
  )
}
