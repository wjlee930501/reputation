import Link from 'next/link'

import { fullClinicAddress } from '@/lib/clinic-schema'

import { CalendarIcon, ExternalIcon, GlobeIcon, MapPinIcon, MessageIcon, NavigationIcon, PhoneIcon } from './icons'
import { regionSentenceLabel } from '@/lib/clinic-region'
import { VISIT_PARKING_DEFAULT, VISIT_TRANSIT_DEFAULT } from '@/lib/clinic-template-copy'

interface ChannelLink {
  url: string | null
  label: string
}

interface Props {
  address: string
  /** 도로명 주소 뒤 상세 주소(건물명·층·호). 없으면 null. */
  addressDetail?: string | null
  phone: string
  links: ChannelLink[]
  googleMapsUrl: string | null
  hospitalName: string
  hospitalRootUrl: string
  region: string[]
  websiteUrl: string | null
  /** 진료시간 표가 있는 곳. /visit에서는 같은 페이지 앵커가 된다. */
  hoursHref: string
  /** 병원 정보의 주차·대중교통 안내(0096). 없으면 기본 안내. */
  parkingNote?: string | null
  transitNote?: string | null
  /** 섹션 설정이 바꾼 제목·안내 문장(레이아웃 편집기). 없으면 지금 문구. */
  title?: string | null
  note?: string | null
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

// 방문 전 확인 — 병원 정보에 쓴 주차·대중교통 안내가 있으면 그 문장을, 없으면 병원 시설을
// 단정하지 않는 기본 안내를 쓴다.
function visitChecks(parkingNote: string | null, transitNote: string | null) {
  return [
    { title: '주차 안내', body: parkingNote || VISIT_PARKING_DEFAULT },
    { title: '대중교통', body: transitNote || VISIT_TRANSIT_DEFAULT },
    { title: '초진 준비물', body: '신분증과 복용 중인 약, 이전 검사 자료가 있으면 지참해 주세요.' },
  ]
}

export function ContactCard({
  address,
  addressDetail = null,
  phone,
  links,
  googleMapsUrl,
  hospitalName,
  hospitalRootUrl,
  region,
  websiteUrl,
  hoursHref,
  parkingNote = null,
  transitNote = null,
  title,
  note,
}: Props) {
  const visibleLinks = links.filter((link) => Boolean(link.url))
  const regionText = regionSentenceLabel(region)
  const hoursIsSamePageAnchor = hoursHref.startsWith('#')

  return (
    <section id="contact" className="clinic-section clinic-section--alt">
      <div className="clinic-section-inner">
        <header className="clinic-section-head">
          <h2 className="clinic-section-title">{title ?? '오시는 길·방문 안내'}</h2>
          <p className="clinic-section-note">
            {note ?? (
              <>
                진료 예약·상담은 아래 병원 공식 채널을 이용해 주세요. 방문 전 확인하면 좋은 안내를 정리했습니다.
              </>
            )}
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
            <Link href={`${hospitalRootUrl}/visit`} className="clinic-visit-action">
              <NavigationIcon aria-hidden="true" />
              <span>길찾기</span>
              <small>오시는 길 안내</small>
            </Link>
          )}
          {/* 같은 페이지를 다시 여는 링크는 환자에게 아무 정보도 주지 않는다.
              /visit에서는 이 페이지의 진료시간 표로 바로 내려간다. */}
          {hoursIsSamePageAnchor ? (
            <a href={hoursHref} className="clinic-visit-action">
              <CalendarIcon aria-hidden="true" />
              <span>진료시간 보기</span>
              <small>이 페이지의 요일별 진료시간</small>
            </a>
          ) : (
            <Link href={hoursHref} className="clinic-visit-action">
              <CalendarIcon aria-hidden="true" />
              <span>진료시간 보기</span>
              <small>요일별 진료 안내</small>
            </Link>
          )}
        </div>

        <div className="clinic-visit-body">
          <div className="clinic-visit-location">
            <span className="clinic-visit-location-pin" aria-hidden="true"><MapPinIcon /></span>
            <span className="clinic-visit-location-label">병원 위치</span>
            {regionText && <span className="clinic-visit-location-region">{regionText}</span>}
            <span className="clinic-visit-location-address">
              {fullClinicAddress(address, addressDetail) || '주소 확인 중'}
            </span>
            {googleMapsUrl && (
              <a href={googleMapsUrl} target="_blank" rel="noopener noreferrer" className="clinic-visit-location-link">
                지도에서 보기
                <ExternalIcon className="clinic-icon clinic-icon--sm" style={{ color: 'currentColor' }} />
              </a>
            )}
          </div>

          <ul className="clinic-visit-checks" aria-label="방문 전 확인">
            {visitChecks(parkingNote, transitNote).map((check) => (
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
