interface Props {
  hospitalName: string
  address: string
  phone: string
}

export function ClinicFooter({ hospitalName, address, phone }: Props) {
  const year = new Date().getFullYear()
  return (
    <footer className="clinic-footer">
      <div className="clinic-footer-inner">
        <div>
          <p className="clinic-footer-name">{hospitalName}</p>
          <p className="clinic-footer-meta">
            {address} · <a href={`tel:${phone}`}>{phone}</a>
          </p>
        </div>

        <div className="clinic-footer-rule" aria-hidden="true" />

        <div className="clinic-footer-disclaimer">
          <span className="clinic-footer-disclaimer-chip">
            <strong>MotionLabs</strong> Research Preview
          </span>
          <span>
            병원 정보·콘텐츠 허브 운영: 주식회사 모션랩스 ·{' '}
            <a href="https://motionlabs.kr" target="_blank" rel="noopener">motionlabs.kr</a>
          </span>
        </div>

        <p className="clinic-footer-meta">
          본 페이지의 의료 정보는 발행 시점 검수를 거친 자료이며, 일반적인 안내 목적입니다. 진료 결정은 의료진과의 상담이 우선합니다. © {year} {hospitalName}.
        </p>
      </div>
    </footer>
  )
}
