import Link from 'next/link'

import type { CareFlowStepView } from '@/lib/clinic-site-config'
import { CARE_FLOW_NOTE, CARE_FLOW_TITLE, CARE_STEPS } from '@/lib/clinic-template-copy'

interface Props {
  hospitalRootUrl: string
  hospitalName: string
  /** 섹션 설정이 바꾼 제목·안내·단계(레이아웃 편집기, `resolveCareFlow`). 없으면 공통 4단계. */
  title?: string | null
  note?: string | null
  steps?: readonly CareFlowStepView[]
}

export function CareFlow({ hospitalRootUrl, title, note, steps = CARE_STEPS }: Props) {
  return (
    <section className="clinic-section clinic-section--flow">
      <div className="clinic-section-inner">
        <header className="clinic-section-head clinic-section-head--center">
          <h2 className="clinic-section-title">{title ?? CARE_FLOW_TITLE}</h2>
          <p className="clinic-section-note">{note ?? CARE_FLOW_NOTE}</p>
        </header>

        <ol className="clinic-flow-timeline" aria-label={`진료 ${steps.length}단계 흐름`}>
          {steps.map((step) => (
            <li key={step.label} className="clinic-flow-node">
              <span className="clinic-flow-node-badge" aria-hidden="true">{step.label}</span>
              <div className="clinic-flow-node-body">
                {step.aux ? <span className="clinic-flow-node-aux">{step.aux}</span> : null}
                <h3 className="clinic-flow-node-title">{step.title}</h3>
                <p className="clinic-flow-node-desc">{step.body}</p>
              </div>
            </li>
          ))}
        </ol>

        <div className="clinic-flow-footer">
          <Link href={`${hospitalRootUrl}/visit`} className="clinic-flow-cta">
            진료 시간·오시는 길 확인
          </Link>
        </div>
      </div>
    </section>
  )
}
