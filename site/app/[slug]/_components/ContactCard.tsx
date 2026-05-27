import { ExternalIcon, GlobeIcon, MessageIcon } from './icons'

const DAY_LABELS: Record<string, string> = {
  mon: '월',
  tue: '화',
  wed: '수',
  thu: '목',
  fri: '금',
  sat: '토',
  sun: '일',
}

const DAY_ORDER = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']

interface ChannelLink {
  url: string | null
  label: string
}

interface Props {
  address: string
  phone: string
  businessHours: Record<string, string> | null | undefined
  links: ChannelLink[]
  googleMapsUrl: string | null
  hospitalName: string
  websiteUrl: string | null
}

function orderedHours(hours: Record<string, string> | null | undefined): Array<[string, string]> {
  if (!hours) return []
  return Object.entries(hours).sort(([a], [b]) => {
    const ai = DAY_ORDER.indexOf(a.toLowerCase())
    const bi = DAY_ORDER.indexOf(b.toLowerCase())
    if (ai === -1 && bi === -1) return a.localeCompare(b)
    if (ai === -1) return 1
    if (bi === -1) return -1
    return ai - bi
  })
}

function hostOf(url: string): string {
  try {
    return new URL(url).host.replace(/^www\./, '')
  } catch {
    return url
  }
}

function pickIcon(label: string): JSX.Element {
  if (label.includes('카카오') || label.includes('kakao')) return <MessageIcon />
  if (label.includes('홈페이지') || label.includes('웹사이트')) return <GlobeIcon />
  return <ExternalIcon />
}

export function ContactCard({
  address,
  phone,
  businessHours,
  links,
  googleMapsUrl,
  hospitalName,
  websiteUrl,
}: Props) {
  const hours = orderedHours(businessHours)
  const visibleLinks = links.filter((link) => Boolean(link.url))

  return (
    <section id="contact" className="clinic-section clinic-section--alt">
      <div className="clinic-section-inner">
        <header className="clinic-section-header">
          <span className="clinic-section-label">오시는 길</span>
          <h2 className="clinic-section-heading">{hospitalName} 진료 안내</h2>
          <p className="clinic-section-lede">
            진료 예약·상담은 아래 병원 공식 채널을 이용해 주세요.
          </p>
        </header>

        <div className="clinic-contact-grid">
          <div className="clinic-contact-item">
            <span className="clinic-contact-item-label">주소</span>
            <span className="clinic-contact-item-value">{address}</span>
            {googleMapsUrl && (
              <a
                href={googleMapsUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="clinic-contact-link"
              >
                지도에서 보기
                <ExternalIcon className="clinic-icon clinic-icon--sm" style={{ color: 'currentColor' }} />
              </a>
            )}
          </div>

          <div className="clinic-contact-item">
            <span className="clinic-contact-item-label">전화</span>
            <span className="clinic-contact-item-value">
              <a href={`tel:${phone}`}>{phone}</a>
            </span>
          </div>

          {hours.length > 0 && (
            <div className="clinic-contact-item">
              <span className="clinic-contact-item-label">진료시간</span>
              <ul className="clinic-contact-hours">
                {hours.map(([day, time]) => (
                  <li key={day}>
                    <span className="clinic-contact-hours-day">{DAY_LABELS[day.toLowerCase()] ?? day}</span>
                    <span className="clinic-contact-hours-time">{time}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>

        {(visibleLinks.length > 0 || websiteUrl) && (
          <>
            <div style={{ height: 32 }} />
            <header className="clinic-section-header" style={{ marginBottom: 18 }}>
              <span className="clinic-section-label">공식 채널</span>
              <h3
                style={{
                  margin: 0,
                  fontSize: 18,
                  fontWeight: 700,
                  color: 'var(--color-revisit-text-title)',
                }}
              >
                {hospitalName} 공식 채널
              </h3>
            </header>
            <div className="clinic-channels" aria-label="병원 공식 외부 채널">
              {websiteUrl && (
                <a
                  href={websiteUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="clinic-channel-card"
                  aria-label="공식 홈페이지로 이동"
                >
                  <span className="clinic-channel-card-icon"><GlobeIcon /></span>
                  <span className="clinic-channel-card-meta">
                    <span className="clinic-channel-card-label">병원 공식 홈페이지</span>
                    <span className="clinic-channel-card-host">{hostOf(websiteUrl)}</span>
                  </span>
                </a>
              )}
              {visibleLinks.map((link) => (
                <a
                  key={link.url ?? link.label}
                  href={link.url ?? '#'}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="clinic-channel-card"
                >
                  <span className="clinic-channel-card-icon">{pickIcon(link.label)}</span>
                  <span className="clinic-channel-card-meta">
                    <span className="clinic-channel-card-label">{link.label}</span>
                    <span className="clinic-channel-card-host">{hostOf(link.url ?? '')}</span>
                  </span>
                </a>
              ))}
            </div>
          </>
        )}
      </div>
    </section>
  )
}
