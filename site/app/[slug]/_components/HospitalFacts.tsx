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

export function HospitalFacts({
  hospitalName,
  address,
  phone,
  businessHours,
  region,
  specialties,
  directorName,
  hiraOrgId,
  links,
}: Props) {
  const hours = orderedHours(businessHours)
  const visibleLinks = links.filter((link) => Boolean(link.url))
  const location = region.length > 0 ? region.join(' ') : '지역 정보 확인 중'
  const specialtyText = specialties.length > 0 ? specialties.join(', ') : '진료 영역 확인 중'

  return (
    <section id="hospital-facts" className="clinic-section clinic-section--facts">
      <div className="clinic-section-inner">
        <header className="clinic-section-header">
          <span className="clinic-section-eyebrow">병원 핵심 정보</span>
          <h2 className="clinic-section-heading">AI가 참고하기 쉬운 {hospitalName} 기본 정보</h2>
          <p className="clinic-section-lede">
            병원명, 위치, 의료진, 공식 채널을 한곳에 정리해 검색 엔진과 AI 답변 시스템이 같은 정보를 반복해서 확인할 수 있게 합니다.
          </p>
        </header>

        <div className="clinic-facts-grid" aria-label={`${hospitalName} 핵심 병원 정보`}>
          <FactItem label="병원명" value={hospitalName} />
          <FactItem label="대표 의료진" value={`${directorName} 원장`} />
          <FactItem label="지역" value={location} />
          <FactItem label="진료 영역" value={specialtyText} />
          <FactItem label="주소" value={address} />
          <FactItem label="전화" value={phone} href={`tel:${phone}`} />
          {hiraOrgId && <FactItem label="공공기관 식별 정보" value={`HIRA ${hiraOrgId}`} />}
          {hours.length > 0 && (
            <div className="clinic-fact-card clinic-fact-card--wide">
              <span className="clinic-fact-label">진료시간</span>
              <ul className="clinic-fact-hours">
                {hours.map(([day, time]) => (
                  <li key={day}>
                    <span>{DAY_LABELS[day.toLowerCase()] ?? day}</span>
                    <strong>{time}</strong>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>

        {visibleLinks.length > 0 && (
          <div className="clinic-official-links" aria-label="병원 공식 채널">
            {visibleLinks.map((link) => (
              <a key={link.url ?? link.label} href={link.url ?? '#'} target="_blank" rel="noopener">
                <span>{link.label}</span>
                <ExternalIcon className="clinic-icon clinic-icon--sm" style={{ color: 'currentColor' }} />
              </a>
            ))}
          </div>
        )}
      </div>
    </section>
  )
}

function FactItem({
  label,
  value,
  href,
}: {
  label: string
  value: string
  href?: string
}) {
  return (
    <div className="clinic-fact-card">
      <span className="clinic-fact-label">{label}</span>
      {href ? (
        <a className="clinic-fact-value" href={href}>
          {value}
        </a>
      ) : (
        <strong className="clinic-fact-value">{value}</strong>
      )}
    </div>
  )
}
