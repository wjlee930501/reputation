import Link from 'next/link'

interface Props {
  hospitalSlug: string
  hospitalName: string
}

const CARE_STEPS = [
  {
    label: '01',
    title: '문진과 이학적 검사',
    body: '통증 위치, 시작 시점, 움직임 제한, 운동·업무 습관을 먼저 확인합니다.',
  },
  {
    label: '02',
    title: '영상검사와 원인 감별',
    body: '필요한 경우 X-ray, 초음파 등 검사를 통해 관절·힘줄·신경 문제를 구분합니다.',
  },
  {
    label: '03',
    title: '단계별 치료 계획',
    body: '약물, 주사, 물리치료, 도수재활 등 비수술 치료부터 상태에 맞게 상담합니다.',
  },
  {
    label: '04',
    title: '재활과 생활 관리',
    body: '통증 재발을 줄이기 위해 운동 복귀 기준과 일상 관리 방법을 함께 안내합니다.',
  },
]

export function CareFlow({ hospitalSlug, hospitalName }: Props) {
  return (
    <section className="clinic-section clinic-section--flow">
      <div className="clinic-section-inner">
        <div className="clinic-flow-layout">
          <header className="clinic-section-header">
            <span className="clinic-section-label">Care Flow</span>
            <h2 className="clinic-section-heading">처음부터 치료를 정하지 않습니다</h2>
            <p className="clinic-section-lede">
              {hospitalName}은 증상과 검사 소견을 함께 확인한 뒤 치료 방향을 상담합니다.
              같은 부위의 통증도 원인과 회복 목표에 따라 계획이 달라질 수 있습니다.
            </p>
            <Link href={`/${hospitalSlug}/visit`} className="clinic-flow-cta">
              진료 시간과 위치 확인
            </Link>
          </header>

          <ol className="clinic-flow-list">
            {CARE_STEPS.map((step) => (
              <li key={step.label} className="clinic-flow-step">
                <span>{step.label}</span>
                <div>
                  <h3>{step.title}</h3>
                  <p>{step.body}</p>
                </div>
              </li>
            ))}
          </ol>
        </div>
      </div>
    </section>
  )
}
