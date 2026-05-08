import { ExternalIcon } from './icons'

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

interface ExternalLink {
  url: string | null
  label: string
}

interface Props {
  address: string
  phone: string
  businessHours: Record<string, string> | null | undefined
  links: ExternalLink[]
  googleMapsUrl: string | null
}

function orderedHours(hours: Record<string, string> | null | undefined): Array<[string, string]> {
  if (!hours) return []
  const entries = Object.entries(hours)
  return entries.sort(([a], [b]) => {
    const ai = DAY_ORDER.indexOf(a.toLowerCase())
    const bi = DAY_ORDER.indexOf(b.toLowerCase())
    if (ai === -1 && bi === -1) return a.localeCompare(b)
    if (ai === -1) return 1
    if (bi === -1) return -1
    return ai - bi
  })
}

export function ContactCard({ address, phone, businessHours, links, googleMapsUrl }: Props) {
  const hours = orderedHours(businessHours)
  const visibleLinks = links.filter((link) => Boolean(link.url))

  return (
    <section id="contact" className="clinic-section clinic-section--alt">
      <div className="clinic-section-inner">
        <header className="clinic-section-header">
          <span className="clinic-section-eyebrow">Visit</span>
          <h2 className="clinic-section-heading">진료 안내</h2>
        </header>

        <div className="clinic-contact-grid">
          <div className="clinic-contact-item">
            <span className="clinic-contact-item-label">주소</span>
            <span className="clinic-contact-item-value">{address}</span>
            {googleMapsUrl && (
              <a
                href={googleMapsUrl}
                target="_blank"
                rel="noopener"
                className="clinic-contact-link"
                style={{ marginTop: 12 }}
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

        {visibleLinks.length > 0 && (
          <div className="clinic-contact-links" aria-label="외부 채널">
            {visibleLinks.map((link) => (
              <a
                key={link.url ?? link.label}
                href={link.url ?? '#'}
                target="_blank"
                rel="noopener"
                className="clinic-contact-link"
              >
                {link.label}
                <ExternalIcon className="clinic-icon clinic-icon--sm" style={{ color: 'currentColor' }} />
              </a>
            ))}
          </div>
        )}
      </div>
    </section>
  )
}
