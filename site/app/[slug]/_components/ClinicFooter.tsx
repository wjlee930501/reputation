import { ExternalIcon, PhoneIcon } from './icons'

interface Props {
  hospitalName: string
  directorName: string
  address: string
  phone: string
  websiteUrl: string | null
}

// 병원 허브의 푸터는 병원 명의로만 구성한다 — 플랫폼(MotionLabs) 약관/개인정보 링크는
// B2B 랜딩 전용이므로 병원 페이지에 노출하지 않는다.
export function ClinicFooter({ hospitalName, directorName, address, phone, websiteUrl }: Props) {
  const year = new Date().getFullYear()
  return (
    <footer className="clinic-footer">
      <div className="clinic-footer-inner">
        <div className="clinic-footer-cta">
          <div className="clinic-footer-cta-copy">
            <strong>진료 문의가 필요하신가요?</strong>
            <span>진료 예약·상담은 대표 전화로 안내해 드립니다.</span>
          </div>
          <a href={`tel:${phone}`} className="clinic-footer-cta-btn">
            <PhoneIcon className="clinic-icon clinic-icon--sm" style={{ color: 'currentColor' }} />
            {phone}
          </a>
        </div>

        <div className="clinic-footer-rule" aria-hidden="true" />

        <div className="clinic-footer-cols">
          <div className="clinic-footer-col">
            <p className="clinic-footer-name">{hospitalName}</p>
            {directorName && <p className="clinic-footer-meta">대표자 {directorName}</p>}
            {websiteUrl && (
              <p className="clinic-footer-meta">
                <a
                  href={websiteUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="clinic-footer-site"
                >
                  병원 공식 홈페이지
                  <ExternalIcon style={{ color: 'currentColor', width: 13, height: 13 }} />
                </a>
              </p>
            )}
          </div>
          <div className="clinic-footer-col">
            <span className="clinic-footer-col-label">연락처</span>
            <p className="clinic-footer-meta">{address}</p>
            <p className="clinic-footer-meta">
              대표전화 <a href={`tel:${phone}`}>{phone}</a>
            </p>
          </div>
        </div>

        <div className="clinic-footer-rule" aria-hidden="true" />

        <p className="clinic-footer-fine">
          이 페이지의 글은 {hospitalName}의 진료 정보를 바탕으로 정리한 일반 건강 정보입니다.
          개인의 증상과 치료 방법은 진료를 통해 달라질 수 있으며, 진료 결정은 의료진과의 상담이 우선합니다.
        </p>
        <p className="clinic-footer-copy">© {year} {hospitalName}.</p>
      </div>
    </footer>
  )
}
