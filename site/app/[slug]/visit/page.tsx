import { Metadata } from 'next'
import { notFound } from 'next/navigation'

import { fetchHospital, HospitalNotFoundError } from '@/lib/api'

import { Breadcrumb, buildBreadcrumbJsonLd } from '../_components/Breadcrumb'
import { ClinicFooter } from '../_components/ClinicFooter'
import { ClinicHeader } from '../_components/ClinicHeader'
import { ContactCard } from '../_components/ContactCard'
import { JsonLd } from '../_components/JsonLd'

interface Props {
  params: { slug: string }
}

export const revalidate = 3600

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL || 'https://reputation.co.kr'

const SCHEMA_DAY_OF_WEEK: Record<string, string> = {
  mon: 'Monday',
  tue: 'Tuesday',
  wed: 'Wednesday',
  thu: 'Thursday',
  fri: 'Friday',
  sat: 'Saturday',
  sun: 'Sunday',
}

const CLOSED_KEYWORDS = ['휴진', '휴무', 'closed']

function isClosedLabel(value: string): boolean {
  const lowered = value.toLowerCase()
  return CLOSED_KEYWORDS.some((kw) => lowered.includes(kw))
}

function extractTimeRanges(value: string): Array<{ opens: string; closes: string }> {
  const ranges: Array<{ opens: string; closes: string }> = []
  for (const segment of value.split(/[,/]|·|및|그리고/)) {
    const trimmed = segment.trim()
    if (!trimmed || isClosedLabel(trimmed)) continue
    const matches = trimmed.match(/\d{1,2}:\d{2}/g)
    if (matches && matches.length >= 2) {
      ranges.push({ opens: matches[0], closes: matches[matches.length - 1] })
    }
  }
  return ranges
}

function buildOpeningHoursSpec(hours: Record<string, string> | null | undefined) {
  if (!hours) return []
  const specs: Array<Record<string, unknown>> = []
  for (const [day, rawValue] of Object.entries(hours)) {
    const value = String(rawValue ?? '')
    const dayOfWeek = SCHEMA_DAY_OF_WEEK[day.toLowerCase()] || day
    if (isClosedLabel(value)) {
      specs.push({
        '@type': 'OpeningHoursSpecification',
        dayOfWeek,
        description: value,
        opens: '00:00',
        closes: '00:00',
      })
      continue
    }
    const ranges = extractTimeRanges(value)
    if (ranges.length === 0) {
      specs.push({
        '@type': 'OpeningHoursSpecification',
        dayOfWeek,
        description: value,
      })
      continue
    }
    for (const range of ranges) {
      specs.push({
        '@type': 'OpeningHoursSpecification',
        dayOfWeek,
        description: value,
        opens: range.opens,
        closes: range.closes,
      })
    }
  }
  return specs
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  try {
    const hospital = await fetchHospital(params.slug)
    const description = `${hospital.name} 진료 안내 — 주소, 전화, 진료시간, 공식 채널. 진료 예약·상담은 병원 공식 채널로 연결됩니다.`
    return {
      title: `진료 안내 | ${hospital.name}`,
      description,
      alternates: { canonical: `/${params.slug}/visit` },
      openGraph: {
        title: `진료 안내 | ${hospital.name}`,
        description,
        url: `/${params.slug}/visit`,
        type: 'website',
      },
    }
  } catch {
    return { title: '진료 안내' }
  }
}

export default async function VisitPage({ params }: Props) {
  let hospital
  try {
    hospital = await fetchHospital(params.slug)
  } catch (e) {
    if (e instanceof HospitalNotFoundError) notFound()
    throw e
  }

  const breadcrumbItems = [
    { label: '홈', href: `/${params.slug}` },
    { label: '진료 안내' },
  ]

  const sameAs = [
    hospital.website_url,
    hospital.blog_url,
    hospital.kakao_channel_url,
    hospital.google_business_profile_url,
    hospital.google_maps_url,
    hospital.naver_place_url,
  ].filter((value): value is string => Boolean(value))

  const visitJsonLd = {
    '@context': 'https://schema.org',
    '@type': ['MedicalClinic', 'LocalBusiness'],
    name: hospital.name,
    url: `${SITE_URL}/${params.slug}/visit`,
    address: {
      '@type': 'PostalAddress',
      streetAddress: hospital.address,
      addressCountry: 'KR',
    },
    telephone: hospital.phone,
    medicalSpecialty: hospital.specialties,
    openingHoursSpecification: buildOpeningHoursSpec(hospital.business_hours),
    hasMap: hospital.google_maps_url || undefined,
    geo:
      hospital.latitude && hospital.longitude
        ? { '@type': 'GeoCoordinates', latitude: hospital.latitude, longitude: hospital.longitude }
        : undefined,
    sameAs: sameAs.length > 0 ? sameAs : undefined,
  }

  const externalChannels = [
    { url: hospital.blog_url, label: '병원 블로그' },
    { url: hospital.kakao_channel_url, label: '카카오톡 상담' },
    { url: hospital.naver_place_url, label: '네이버 플레이스' },
    { url: hospital.google_business_profile_url, label: 'Google 비즈니스 프로필' },
  ]

  return (
    <>
      <JsonLd data={[visitJsonLd, buildBreadcrumbJsonLd(breadcrumbItems, SITE_URL)]} />
      <div className="clinic-shell">
        <ClinicHeader
          hospitalName={hospital.name}
          hospitalSlug={params.slug}
          region={hospital.region}
          specialties={hospital.specialties}
          phone={hospital.phone}
          websiteUrl={hospital.website_url}
        />
        <main>
          <section className="clinic-library-hero">
            <div className="clinic-library-hero-inner">
              <Breadcrumb items={breadcrumbItems} />
              <span className="clinic-section-eyebrow">진료 안내</span>
              <h1 className="clinic-library-hero-title">{hospital.name} 진료 안내</h1>
              <p className="clinic-library-hero-meta">
                <strong>{hospital.address}</strong>
                <span className="clinic-library-divider-dot" aria-hidden="true" />
                <a
                  href={`tel:${hospital.phone}`}
                  style={{ color: 'var(--color-revisit-primary-40)', fontWeight: 700 }}
                >
                  {hospital.phone}
                </a>
              </p>
              <p
                className="clinic-section-lede"
                style={{ marginTop: 16, maxWidth: 720, fontSize: 14 }}
              >
                진료 예약·상담은 아래 병원 공식 채널을 통해 직접 연결해 주세요.
              </p>
            </div>
          </section>

          <ContactCard
            address={hospital.address}
            phone={hospital.phone}
            businessHours={hospital.business_hours}
            googleMapsUrl={hospital.google_maps_url}
            links={externalChannels}
            hospitalName={hospital.name}
            websiteUrl={hospital.website_url}
          />
        </main>
        <ClinicFooter
          hospitalName={hospital.name}
          address={hospital.address}
          phone={hospital.phone}
          websiteUrl={hospital.website_url}
        />
      </div>
    </>
  )
}
