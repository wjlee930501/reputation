import { ExternalIcon } from './icons'

interface Props {
  hospitalName: string
  address: string
  phone: string
  websiteUrl: string | null
}

export function ClinicFooter({ hospitalName, address, phone, websiteUrl }: Props) {
  const year = new Date().getFullYear()
  return (
    <footer className="clinic-footer">
      <div className="clinic-footer-inner">
        <div>
          <p className="clinic-footer-name">{hospitalName}</p>
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
          <span>
            이 블로그의 글은 {hospitalName}의 진료 정보를 바탕으로 정리한 일반 건강 정보입니다.
          </span>
        </div>

        <p className="clinic-footer-meta">
          개인의 증상과 치료 방법은 진료를 통해 달라질 수 있습니다. 진료 결정은 의료진과의 상담이 우선합니다.
          © {year} {hospitalName}.
        </p>
      </div>
    </footer>
  )
}
