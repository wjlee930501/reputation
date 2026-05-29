import Link from 'next/link'

interface Props {
  hospitalSlug: string
  hospitalName: string
}

const CARE_STEPS = [
  {
    label: '01',
    title: '문진·이학적 검사',
    body: '통증 위치, 시작 시점, 움직임 제한, 운동·업무 습관을 확인합니다.',
  },
  {
    label: '02',
    title: '영상검사·원인 감별',
    body: 'X-ray, 초음파로 관절·힘줄·신경 문제를 구분합니다.',
  },
  {
    label: '03',
    title: '단계별 치료 계획',
    body: '비수술 치료부터 환자 상태에 맞게 선택지를 설명합니다.',
  },
  {
    label: '04',
    title: '재활·생활 관리',
    body: '운동 복귀 기준과 재발 예방 방법을 함께 안내합니다.',
  },
]

export function CareFlow({ hospitalSlug, hospitalName }: Props) {
  return (
    <section className="clinic-section clinic-section--flow">
      <div className="clinic-section-inner">
        <header className="clinic-section-header clinic-section-header--center">
          <span className="clinic-section-label">진료 흐름</span>
          <h2 className="clinic-section-heading">{hospitalName} 진료 4단계</h2>
          <p className="clinic-section-lede">
            첫 방문부터 재활까지 — 진찰과 검사 소견을 바탕으로 단계적으로 진행합니다.
          </p>
        </header>

        <ol className="clinic-flow-timeline" aria-label="진료 4단계 흐름">
          {CARE_STEPS.map((step, idx) => (
            <li key={step.label} className="clinic-flow-node">
              {/* 연결선: 마지막 항목 제외 */}
              {idx < CARE_STEPS.length - 1 && (
                <span className="clinic-flow-connector" aria-hidden="true" />
              )}
              <span className="clinic-flow-node-badge" aria-hidden="true">{step.label}</span>
              <div className="clinic-flow-node-body">
                <h3 className="clinic-flow-node-title">{step.title}</h3>
                <p className="clinic-flow-node-desc">{step.body}</p>
              </div>
            </li>
          ))}
        </ol>

        <div className="clinic-flow-footer">
          <Link href={`/${hospitalSlug}/visit`} className="clinic-flow-cta">
            진료 시간·오시는 길 확인
          </Link>
        </div>
      </div>
    </section>
  )
}
