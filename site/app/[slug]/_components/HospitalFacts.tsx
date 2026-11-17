import { seoulToday } from '@/lib/clinic-layout'
import { fullClinicAddress } from '@/lib/clinic-schema'

import { WeekHours } from './WeekHours'
import { MapPinIcon, NavigationIcon, PhoneIcon, StethoscopeIcon } from './icons'
import { FACTS_SECTION_NOTE, factsSectionTitle } from '@/lib/clinic-template-copy'
import { regionSentenceLabel } from '@/lib/clinic-region'

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


function isClosed(time: string): boolean {
  return /휴진|휴무|closed/i.test(time)
}

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
  // 서버(UTC)가 아닌 한국 시간 기준 오늘. 캐시된 HTML의 날짜는 WeekHours가 브라우저에서 바로잡는다.
  const { key: todayKey, month, day } = seoulToday()
  const renderedToday = { key: todayKey, month, day }
  const week = DAY_ORDER.map((key) => ({
    key,
    label: DAY_LABELS[key] ?? key,
    time: businessHours?.[key] ?? null,
  }))
  const hasHours = week.some((d) => d.time)
  const visibleLinks = links.filter((link) => Boolean(link.url))
  const location = regionSentenceLabel(region) || '지역 정보 확인 중'
  const specialtyText = specialties.length > 0 ? specialties.join(', ') : '진료 영역 확인 중'
  const closedDays = week.filter((d) => d.time && isClosed(d.time)).map((d) => d.label)

  return (
    <section id="hospital-facts" className="clinic-section clinic-section--facts">
      <div className="clinic-section-inner">
        <header className="clinic-section-head">
          <h2 className="clinic-section-title">{factsSectionTitle(hospitalName)}</h2>
          <p className="clinic-section-note">{FACTS_SECTION_NOTE}</p>
        </header>

        {hasHours && (
          <div className="clinic-week" aria-label="주간 진료시간">
            <WeekHours businessHours={businessHours} renderedToday={renderedToday} />
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
            <span className="clinic-keyfact-value">
              {fullClinicAddress(address, addressDetail) || '주소 확인 중'}
            </span>
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
