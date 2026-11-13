import Link from 'next/link'

import { CARE_FLOW_NOTE, CARE_FLOW_TITLE, CARE_STEPS } from '@/lib/clinic-template-copy'

interface Props {
  hospitalRootUrl: string
  hospitalName: string
}

export function CareFlow({ hospitalRootUrl }: Props) {
  return (
    <section className="clinic-section clinic-section--flow">
      <div className="clinic-section-inner">
        <header className="clinic-section-head clinic-section-head--center">
          <h2 className="clinic-section-title">{CARE_FLOW_TITLE}</h2>
          <p className="clinic-section-note">{CARE_FLOW_NOTE}</p>
        </header>

        <ol className="clinic-flow-timeline" aria-label="진료 4단계 흐름">
          {CARE_STEPS.map((step) => (
            <li key={step.label} className="clinic-flow-node">
              <span className="clinic-flow-node-badge" aria-hidden="true">{step.label}</span>
              <div className="clinic-flow-node-body">
                <span className="clinic-flow-node-aux">{step.aux}</span>
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
