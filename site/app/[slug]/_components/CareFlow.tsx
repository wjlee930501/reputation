import Link from 'next/link'

interface Props {
  hospitalSlug: string
  hospitalName: string
}

/* 특정 검사 장비·치료 방식(X-ray·초음파·도수치료 등)을 병원이 제공하는 것처럼
   단정하지 않는다 — 프로파일에 없는 임상 정보는 일반적인 상담·진단·치료·관리
   단계로만 안내한다. */
const CARE_STEPS = [
  {
    label: '01',
    title: '상담·문진',
    body: '증상과 불편한 점, 생활 습관과 병력을 충분히 듣고 확인합니다.',
  },
  {
    label: '02',
    title: '진단·원인 확인',
    body: '진찰 소견을 바탕으로 필요한 검사를 안내하고 증상의 원인을 확인합니다.',
  },
  {
    label: '03',
    title: '치료 계획 상담',
    body: '확인된 결과를 바탕으로 치료 선택지를 설명하고 함께 결정합니다.',
  },
  {
    label: '04',
    title: '경과 확인·생활 관리',
    body: '치료 후 경과를 확인하고 일상에서의 관리 방법을 안내합니다.',
  },
]

export function CareFlow({ hospitalSlug }: Props) {
  return (
    <section className="clinic-section clinic-section--flow">
      <div className="clinic-section-inner">
        <header className="clinic-section-head clinic-section-head--center">
          <h2 className="clinic-section-title">첫 방문부터 경과 관리까지, 진료 4단계</h2>
          <p className="clinic-section-note">
            상담과 진료 확인을 바탕으로 단계적으로 진행합니다.
          </p>
        </header>

        <ol className="clinic-flow-timeline" aria-label="진료 4단계 흐름">
          {CARE_STEPS.map((step) => (
            <li key={step.label} className="clinic-flow-node">
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
