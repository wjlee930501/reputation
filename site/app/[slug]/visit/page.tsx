import { Metadata } from 'next'
import { notFound } from 'next/navigation'

import { fetchHospital, HospitalNotFoundError } from '@/lib/api'
import { buildOpeningHoursSpec } from '@/lib/business-hours'
import { buildClinicThemeStyle } from '@/lib/clinic-theme'
import { canonicalHospitalUrl } from '@/lib/site-url'

import { Breadcrumb, buildBreadcrumbJsonLd } from '../_components/Breadcrumb'
import { ClinicFooter } from '../_components/ClinicFooter'
import { ClinicHeader } from '../_components/ClinicHeader'
import { ContactCard } from '../_components/ContactCard'
import { JsonLd } from '../_components/JsonLd'

interface Props {
  params: Promise<{ slug: string }>
}

export const revalidate = 3600

export async function generateMetadata({ params: paramsPromise }: Props): Promise<Metadata> {
  const params = await paramsPromise
  try {
    const hospital = await fetchHospital(params.slug)
    const description = `${hospital.name} 진료 안내 — 주소, 전화, 진료시간, 공식 채널. 진료 예약·상담은 병원 공식 채널로 연결됩니다.`
    const canonicalUrl = canonicalHospitalUrl(hospital, params.slug, 'visit')
    return {
      title: `진료 안내 | ${hospital.name}`,
      description,
      alternates: { canonical: canonicalUrl },
      openGraph: {
        title: `진료 안내 | ${hospital.name}`,
        description,
        url: canonicalUrl,
        type: 'website',
      },
    }
  } catch {
    return { title: '진료 안내' }
  }
}

export default async function VisitPage({ params: paramsPromise }: Props) {
  const params = await paramsPromise
  let hospital
  try {
    hospital = await fetchHospital(params.slug)
  } catch (e) {
    if (e instanceof HospitalNotFoundError) notFound()
    throw e
  }

  const hospitalRootUrl = canonicalHospitalUrl(hospital, params.slug)
  const breadcrumbItems = [
    { label: '홈', href: hospitalRootUrl },
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
    // 허브 페이지와 동일한 @id — 검색엔진이 같은 병원 엔티티로 병합하도록 한다.
    '@id': `${hospitalRootUrl}#clinic`,
    name: hospital.name,
    url: `${hospitalRootUrl}/visit`,
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
      <JsonLd data={[visitJsonLd, buildBreadcrumbJsonLd(breadcrumbItems, hospitalRootUrl)]} />
      <div className="clinic-shell clinic-shell--editorial" style={buildClinicThemeStyle(hospital)}>
        <ClinicHeader
          hospitalName={hospital.name}
          hospitalRootUrl={hospitalRootUrl}
          region={hospital.region}
          specialties={hospital.specialties}
          phone={hospital.phone}
          websiteUrl={hospital.website_url}
          logoUrl={hospital.logo_url}
        />
        <main id="main-content">
          <section className="clinic-library-hero">
            <div className="clinic-library-hero-inner">
              <Breadcrumb items={breadcrumbItems} />
              <span className="clinic-section-label">진료 안내</span>
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
            hospitalRootUrl={hospitalRootUrl}
            region={hospital.region}
            websiteUrl={hospital.website_url}
          />
        </main>
        <ClinicFooter
          hospitalName={hospital.name}
          directorName={hospital.director_name}
          address={hospital.address}
          phone={hospital.phone}
          websiteUrl={hospital.website_url}
        />
      </div>
    </>
  )
}
