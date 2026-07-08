import Link from 'next/link'

import { CalendarIcon, ExternalIcon, GlobeIcon, MapPinIcon, MessageIcon, NavigationIcon, PhoneIcon } from './icons'

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
  hospitalSlug: string
  region: string[]
  websiteUrl: string | null
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

// 방문 전 확인용 일반 안내 — 병원별 실제 시설 정보를 단정하지 않는 비임상 체크리스트.
const VISIT_CHECKS = [
  { title: '주차 안내', body: '방문 전 주차 가능 여부와 인근 주차장을 전화로 확인해 주세요.' },
  { title: '대중교통', body: '가까운 지하철역·버스 정류장 하차 후 도보 이동을 권장합니다.' },
  { title: '초진 준비물', body: '신분증과 복용 중인 약, 이전 검사 자료가 있으면 지참해 주세요.' },
]

export function ContactCard({
  address,
  phone,
  links,
  googleMapsUrl,
  hospitalName,
  hospitalSlug,
  region,
  websiteUrl,
}: Props) {
  const visibleLinks = links.filter((link) => Boolean(link.url))
  const regionText = region.filter(Boolean).join(' ')

  return (
    <section id="contact" className="clinic-section clinic-section--alt">
      <div className="clinic-section-inner">
        <header className="clinic-section-head">
          <h2 className="clinic-section-title">오시는 길·방문 안내</h2>
          <p className="clinic-section-note">
            진료 예약·상담은 아래 병원 공식 채널을 이용해 주세요. 방문 전 확인하면 좋은 안내를 정리했습니다.
          </p>
        </header>

        <div className="clinic-visit-actions" aria-label="방문 행동">
          <a href={`tel:${phone}`} className="clinic-visit-action clinic-visit-action--primary">
            <PhoneIcon aria-hidden="true" />
            <span>전화하기</span>
            <small>{phone}</small>
          </a>
          {googleMapsUrl ? (
            <a href={googleMapsUrl} target="_blank" rel="noopener noreferrer" className="clinic-visit-action">
              <NavigationIcon aria-hidden="true" />
              <span>길찾기</span>
              <small>지도에서 위치 보기</small>
            </a>
          ) : (
            <Link href={`/${hospitalSlug}/visit`} className="clinic-visit-action">
              <NavigationIcon aria-hidden="true" />
              <span>길찾기</span>
              <small>오시는 길 안내</small>
            </Link>
          )}
          <Link href={`/${hospitalSlug}/visit`} className="clinic-visit-action">
            <CalendarIcon aria-hidden="true" />
            <span>진료시간 보기</span>
            <small>요일별 진료 안내</small>
          </Link>
        </div>

        <div className="clinic-visit-body">
          <div className="clinic-visit-location">
            <span className="clinic-visit-location-pin" aria-hidden="true"><MapPinIcon /></span>
            <span className="clinic-visit-location-label">병원 위치</span>
            {regionText && <span className="clinic-visit-location-region">{regionText}</span>}
            <span className="clinic-visit-location-address">{address || '주소 확인 중'}</span>
            {googleMapsUrl && (
              <a href={googleMapsUrl} target="_blank" rel="noopener noreferrer" className="clinic-visit-location-link">
                지도에서 보기
                <ExternalIcon className="clinic-icon clinic-icon--sm" style={{ color: 'currentColor' }} />
              </a>
            )}
          </div>

          <ul className="clinic-visit-checks" aria-label="방문 전 확인">
            {VISIT_CHECKS.map((check) => (
              <li key={check.title} className="clinic-visit-check">
                <span className="clinic-visit-check-title">{check.title}</span>
                <span className="clinic-visit-check-body">{check.body}</span>
              </li>
            ))}
          </ul>
        </div>

        {(visibleLinks.length > 0 || websiteUrl) && (
          <div className="clinic-visit-channels-block">
            <span className="clinic-visit-channels-label">{hospitalName} 공식 채널</span>
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
          </div>
        )}
      </div>
    </section>
  )
}
