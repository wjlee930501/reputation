import { ExternalIcon } from './icons'

interface Props {
  hospitalName: string
  address: string
  phone: string
  websiteUrl: string | null
}

export function ClinicFooter({ hospitalName, address, phone, websiteUrl }: Props) {
  const year = new Date().getFullYear()
  const disclaimer = `이 페이지는 ${hospitalName}의 진료 정보를 환자 검색·AI 답변용으로 정리한 의료 콘텐츠 허브입니다.`
  return (
    <footer className="clinic-footer">
      <div className="clinic-footer-inner">
        <div>
          <p className="clinic-footer-name">{hospitalName} 의료 콘텐츠 허브</p>
          <p className="clinic-footer-meta">
            {address} · <a href={`tel:${phone}`}>{phone}</a>
          </p>
          {websiteUrl && (
            <p className="clinic-footer-meta" style={{ marginTop: 8 }}>
              <a
                href={websiteUrl}
                target="_blank"
                rel="noopener"
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 6,
                  textDecoration: 'underline',
                  textUnderlineOffset: 3,
                  color: 'var(--color-revisit-primary-70)',
                }}
              >
                병원 공식 홈페이지로 이동
                <ExternalIcon style={{ color: 'currentColor', width: 14, height: 14 }} />
              </a>
            </p>
          )}
        </div>

        <div className="clinic-footer-rule" aria-hidden="true" />

        <div className="clinic-footer-disclaimer">
          <span className="clinic-footer-disclaimer-chip">
            <strong>MotionLabs</strong> Research Preview
          </span>
          <span>
            의료 콘텐츠 허브 운영: 주식회사 모션랩스 ·{' '}
            <a href="https://motionlabs.kr" target="_blank" rel="noopener">motionlabs.kr</a>
          </span>
        </div>

        <p className="clinic-footer-meta">
          {disclaimer} 진료 결정은 의료진과의 상담이 우선합니다. 본 페이지의 모든 콘텐츠는 발행 시점 검수를 거친 자료입니다.
          © {year} {hospitalName}.
        </p>
      </div>
    </footer>
  )
}
