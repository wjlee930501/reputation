import { CalendarIcon, MapPinIcon, NavigationIcon, PhoneIcon, StethoscopeIcon } from './icons'

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

interface OfficialLink {
  label: string
  url: string | null
}

interface Props {
  hospitalName: string
  address: string
  phone: string
  businessHours: Record<string, string> | null | undefined
  region: string[]
  specialties: string[]
  directorName: string
  hiraOrgId: string | null
  links: OfficialLink[]
  googleMapsUrl?: string | null
}

// 서버(UTC)가 아닌 한국 시간 기준 요일 키 — KST 00:00~09:00 사이 전날 표기 방지.
function seoulDayKey(): string {
  return new Intl.DateTimeFormat('en-US', { weekday: 'short', timeZone: 'Asia/Seoul' })
    .format(new Date())
    .toLowerCase()
}

function isClosed(time: string): boolean {
  return /휴진|휴무|closed/i.test(time)
}

export function HospitalFacts({
  hospitalName,
  address,
  phone,
  businessHours,
  region,
  specialties,
  hiraOrgId,
  links,
  googleMapsUrl,
}: Props) {
  const today = seoulDayKey()
  const week = DAY_ORDER.map((key) => ({
    key,
    label: DAY_LABELS[key] ?? key,
    time: businessHours?.[key] ?? null,
    isToday: key === today,
  }))
  const hasHours = week.some((d) => d.time)
  const visibleLinks = links.filter((link) => Boolean(link.url))
  const location = region.length > 0 ? region.join(' ') : '지역 정보 확인 중'
  const specialtyText = specialties.length > 0 ? specialties.join(', ') : '진료 영역 확인 중'
  const closedDays = week.filter((d) => d.time && isClosed(d.time)).map((d) => d.label)

  return (
    <section id="hospital-facts" className="clinic-section clinic-section--facts">
      <div className="clinic-section-inner">
        <header className="clinic-section-head">
          <h2 className="clinic-section-title">{hospitalName} 기본 정보</h2>
          <p className="clinic-section-note">
            진료시간과 연락처, 위치를 한눈에 확인할 수 있습니다.
          </p>
        </header>

        {hasHours && (
          <div className="clinic-week" aria-label="주간 진료시간">
            <div className="clinic-week-head">
              <CalendarIcon className="clinic-icon clinic-icon--sm" aria-hidden="true" />
              <span>주간 진료시간</span>
              <span className="clinic-week-today-hint">오늘 {DAY_LABELS[today] ?? ''}요일</span>
            </div>
            <ol className="clinic-week-grid">
              {week.map((day) => {
                const closed = day.time ? isClosed(day.time) : false
                return (
                  <li
                    key={day.key}
                    className={`clinic-week-day${day.isToday ? ' is-today' : ''}${closed ? ' is-closed' : ''}`}
                    aria-current={day.isToday ? 'date' : undefined}
                  >
                    <span className="clinic-week-day-label">{day.label}</span>
                    <span className="clinic-week-day-time">
                      {day.time ? (closed ? '휴진' : day.time) : '-'}
                    </span>
                  </li>
                )
              })}
            </ol>
            {closedDays.length > 0 && (
              <p className="clinic-week-notice">
                <span aria-hidden="true" className="clinic-week-notice-dot" />
                휴진 안내 — {closedDays.join(', ')}요일은 진료하지 않습니다. 방문 전 전화로 확인해 주세요.
              </p>
            )}
          </div>
        )}

        <div className="clinic-keyfacts" aria-label={`${hospitalName} 핵심 정보`}>
          <a className="clinic-keyfact clinic-keyfact--action" href={`tel:${phone}`}>
            <span className="clinic-keyfact-icon"><PhoneIcon aria-hidden="true" /></span>
            <span className="clinic-keyfact-label">전화 문의</span>
            <span className="clinic-keyfact-value">{phone}</span>
          </a>

          <div className="clinic-keyfact">
            <span className="clinic-keyfact-icon"><MapPinIcon aria-hidden="true" /></span>
            <span className="clinic-keyfact-label">주소</span>
            <span className="clinic-keyfact-value">{address || '주소 확인 중'}</span>
            {googleMapsUrl && (
              <a className="clinic-keyfact-link" href={googleMapsUrl} target="_blank" rel="noopener noreferrer">
                <NavigationIcon className="clinic-icon clinic-icon--sm" style={{ color: 'currentColor' }} />
                길찾기
              </a>
            )}
          </div>

          <div className="clinic-keyfact">
            <span className="clinic-keyfact-icon"><StethoscopeIcon aria-hidden="true" /></span>
            <span className="clinic-keyfact-label">진료 영역 · 지역</span>
            <span className="clinic-keyfact-value">{specialtyText}</span>
            <span className="clinic-keyfact-sub">{location}</span>
          </div>
        </div>

        {(visibleLinks.length > 0 || hiraOrgId) && (
          <div className="clinic-official-links" aria-label="병원 공식 채널">
            {visibleLinks.map((link) => (
              <a key={link.url ?? link.label} href={link.url ?? '#'} target="_blank" rel="noopener">
                <span>{link.label}</span>
              </a>
            ))}
            {hiraOrgId && <span className="clinic-official-hira">공공기관 식별정보 HIRA {hiraOrgId}</span>}
          </div>
        )}
      </div>
    </section>
  )
}
