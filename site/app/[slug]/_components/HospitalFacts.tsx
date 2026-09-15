import { fullClinicAddress } from '@/lib/clinic-schema'

import { CalendarIcon, ExternalIcon, MapPinIcon, NavigationIcon, PhoneIcon, StethoscopeIcon } from './icons'

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
  /** 도로명 주소 뒤 상세 주소(건물명·층·호). 없으면 null. */
  addressDetail?: string | null
  phone: string
  businessHours: Record<string, string> | null | undefined
  region: string[]
  specialties: string[]
  directorName: string
  hiraOrgId: string | null
  links: OfficialLink[]
  googleMapsUrl?: string | null
}

// 방문 전 확인용 일반 안내 — 병원별 실제 시설 정보를 단정하지 않는 비임상 체크리스트.
const VISIT_CHECKS = [
  { title: '주차 안내', body: '방문 전 주차 가능 여부와 인근 주차장을 전화로 확인해 주세요.' },
  { title: '대중교통', body: '가까운 지하철역·버스 정류장 하차 후 도보 이동을 권장합니다.' },
  { title: '초진 준비물', body: '신분증과 복용 중인 약, 이전 검사 자료가 있으면 지참해 주세요.' },
]

// 서버(UTC)가 아닌 한국 시간 기준 요일 키 — KST 00:00~09:00 사이 전날 표기 방지.
function seoulDayKey(): string {
  return new Intl.DateTimeFormat('en-US', { weekday: 'short', timeZone: 'Asia/Seoul' })
    .format(new Date())
    .toLowerCase()
}

function isClosed(time: string): boolean {
  return /휴진|휴무|closed/i.test(time)
}

/**
 * `09:00 ~ 18:30 (점심 13:00 ~ 14:00)` → `09:00~18:30` / `점심 13:00~14:00`.
 * 요일 칸은 좁다. 물결·하이픈 양옆 공백을 지우고 괄호 안(점심시간 표기는 병원마다
 * 다르다)을 둘째 줄로 내려야 한 칸 안에서 줄이 제멋대로 갈라지지 않는다.
 */
function splitHours(time: string): string {
  return time
    .replace(/\s*([~\-–])\s*/g, '$1')
    .replace(/\s*\(([^)]*)\)\s*/, '\n$1')
}

/**
 * 진료시간·오시는 길 — 주소·전화·진료시간이 페이지에서 자세히 나오는 유일한 자리.
 * 첫 화면 팩트 줄은 요약이고, 푸터는 명의다. 옛 홈은 같은 값을 네 섹션에 반복했다.
 */
export function HospitalFacts({
  hospitalName,
  address,
  addressDetail = null,
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
  const visibleLinks = links.filter((link): link is { label: string; url: string } => Boolean(link.url))
  const location = region.length > 0 ? region.join(' ') : '지역 정보 확인 중'
  const specialtyText = specialties.length > 0 ? specialties.join(', ') : '진료 영역 확인 중'
  const closedDays = week.filter((d) => d.time && isClosed(d.time)).map((d) => d.label)

  return (
    <section id="contact" className="hub-section">
      <div className="hub-container">
        <header className="hub-section-head">
          <h2 className="hub-section-title">진료시간·오시는 길</h2>
          <p className="hub-section-note">
            {hospitalName}의 요일별 진료시간과 연락처, 위치입니다. 진료 예약·상담은 대표 전화로 안내해 드립니다.
          </p>
        </header>

        <div className="hub-facts">
          {hasHours ? (
            <div className="hub-card hub-week" aria-label="주간 진료시간">
              <div className="hub-week-head">
                <CalendarIcon className="hub-icon hub-icon--sm" />
                <span>주간 진료시간</span>
                <span className="hub-week-today">오늘 {DAY_LABELS[today] ?? ''}요일</span>
              </div>
              <ol className="hub-week-grid">
                {week.map((day) => {
                  const closed = day.time ? isClosed(day.time) : false
                  return (
                    <li
                      key={day.key}
                      className={`hub-week-day${day.isToday ? ' is-today' : ''}${closed ? ' is-closed' : ''}`}
                      aria-current={day.isToday ? 'date' : undefined}
                    >
                      <span className="hub-week-day-label">{day.label}</span>
                      <span className="hub-week-day-time">
                        {day.time ? (closed ? '휴진' : splitHours(day.time)) : '-'}
                      </span>
                    </li>
                  )
                })}
              </ol>
              {closedDays.length > 0 && (
                <p className="hub-week-notice">
                  {closedDays.join(', ')}요일은 진료하지 않습니다. 방문 전 전화로 확인해 주세요.
                </p>
              )}
            </div>
          ) : null}

          <div className="hub-card hub-contact" aria-label={`${hospitalName} 연락처`}>
            <div className="hub-contact-row">
              <PhoneIcon className="hub-icon" />
              <span className="hub-contact-label">전화 문의</span>
              <a className="hub-contact-value hub-contact-value--phone" href={`tel:${phone}`}>{phone}</a>
            </div>
            <div className="hub-contact-row">
              <MapPinIcon className="hub-icon" />
              <span className="hub-contact-label">주소</span>
              <span className="hub-contact-value">
                {fullClinicAddress(address, addressDetail) || '주소 확인 중'}
              </span>
              {googleMapsUrl && (
                <a className="hub-contact-link" href={googleMapsUrl} target="_blank" rel="noopener noreferrer">
                  <NavigationIcon className="hub-icon hub-icon--sm" style={{ color: 'currentColor' }} />
                  지도에서 길찾기
                </a>
              )}
            </div>
            <div className="hub-contact-row">
              <StethoscopeIcon className="hub-icon" />
              <span className="hub-contact-label">진료 영역 · 지역</span>
              <span className="hub-contact-value">{specialtyText}</span>
              <span className="hub-contact-sub">{location}</span>
            </div>
          </div>
        </div>

        {(visibleLinks.length > 0 || hiraOrgId) && (
          <div className="hub-facts-foot" aria-label="병원 공식 채널">
            {visibleLinks.length > 0 && (
              <>
                <span className="hub-facts-foot-label">{hospitalName} 공식 채널</span>
                <div className="hub-chip-row">
                  {visibleLinks.map((link) => (
                    <a key={link.url} href={link.url} target="_blank" rel="noopener noreferrer" className="hub-chip-link">
                      {link.label}
                      <ExternalIcon className="hub-icon hub-icon--sm" style={{ color: 'currentColor' }} />
                    </a>
                  ))}
                </div>
              </>
            )}
            {hiraOrgId && <span className="hub-hira">공공기관 식별정보 HIRA {hiraOrgId}</span>}
          </div>
        )}

        <ul className="hub-checks" aria-label="방문 전 확인">
          {VISIT_CHECKS.map((check) => (
            <li key={check.title}>
              <span className="hub-check-title">{check.title}</span>
              <span className="hub-check-body">{check.body}</span>
            </li>
          ))}
        </ul>
      </div>
    </section>
  )
}
