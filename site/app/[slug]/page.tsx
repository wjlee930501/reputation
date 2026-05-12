import { Metadata } from 'next'
import { notFound } from 'next/navigation'

import { fetchHospital, fetchContents } from '@/lib/api'

import { buildBreadcrumbJsonLd } from './_components/Breadcrumb'
import { ClinicFooter } from './_components/ClinicFooter'
import { ClinicGallery } from './_components/ClinicGallery'
import { ClinicHeader } from './_components/ClinicHeader'
import { ClinicHero } from './_components/ClinicHero'
import { ContactCard } from './_components/ContactCard'
import { DoctorIntro } from './_components/DoctorIntro'
import { FeaturedContent } from './_components/FeaturedContent'
import { JsonLd } from './_components/JsonLd'
import { TreatmentGrid } from './_components/TreatmentGrid'

interface Props {
  params: { slug: string }
}

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

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  try {
    const hospital = await fetchHospital(params.slug)
    const description = `${hospital.name} 진료 블로그 — 환자가 자주 묻는 질문, 질환 정보, 치료 안내를 의료진이 알기 쉽게 정리합니다.`
    return {
      title: `${hospital.name} 진료 블로그`,
      description,
      alternates: { canonical: `/${params.slug}` },
      openGraph: {
        title: `${hospital.name} 진료 블로그`,
        description,
        url: `/${params.slug}`,
        type: 'website',
      },
    }
  } catch {
    return { title: '진료 블로그' }
  }
}

export default async function HospitalHubPage({ params }: Props) {
  let hospital
  let contents
  try {
    ;[hospital, contents] = await Promise.all([
      fetchHospital(params.slug),
      fetchContents(params.slug, 60),
    ])
  } catch {
    notFound()
  }

  const sameAs = [
    hospital.website_url,
    hospital.blog_url,
    hospital.kakao_channel_url,
    hospital.google_business_profile_url,
    hospital.google_maps_url,
    hospital.naver_place_url,
  ].filter((value): value is string => Boolean(value))

  const clinicJsonLd = {
    '@context': 'https://schema.org',
    '@type': ['MedicalClinic', 'LocalBusiness'],
    name: hospital.name,
    url: `${SITE_URL}/${params.slug}`,
    image: hospital.director_photo_url ?? undefined,
    sameAs,
    address: {
      '@type': 'PostalAddress',
      streetAddress: hospital.address,
      addressCountry: 'KR',
    },
    telephone: hospital.phone,
    medicalSpecialty: hospital.specialties,
    openingHoursSpecification: Object.entries(hospital.business_hours || {}).map(([day, hours]) => ({
      '@type': 'OpeningHoursSpecification',
      dayOfWeek: SCHEMA_DAY_OF_WEEK[day] || day,
      description: String(hours),
    })),
    hasMap: hospital.google_maps_url || undefined,
    geo:
      hospital.latitude && hospital.longitude
        ? {
            '@type': 'GeoCoordinates',
            latitude: hospital.latitude,
            longitude: hospital.longitude,
          }
        : undefined,
    physician: {
      '@type': 'Physician',
      name: hospital.director_name,
      description: hospital.director_career,
      image: hospital.director_photo_url ?? undefined,
    },
    availableService: (hospital.treatments || []).map((treatment) => ({
      '@type': 'MedicalProcedure',
      name: treatment.name,
    })),
  }

  const breadcrumbJsonLd = buildBreadcrumbJsonLd(
    [{ label: '홈', href: `/${params.slug}` }],
    SITE_URL,
  )

  const externalChannels = [
    { url: hospital.blog_url, label: '병원 블로그' },
    { url: hospital.kakao_channel_url, label: '카카오톡 상담' },
    { url: hospital.naver_place_url, label: '네이버 플레이스' },
    { url: hospital.google_business_profile_url, label: 'Google 비즈니스 프로필' },
  ]

  return (
    <>
      <JsonLd data={[clinicJsonLd, breadcrumbJsonLd]} />
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
          <ClinicHero
            hospitalName={hospital.name}
            hospitalSlug={params.slug}
            region={hospital.region}
            specialties={hospital.specialties}
            phone={hospital.phone}
            directorName={hospital.director_name}
            directorPhotoUrl={hospital.director_photo_url}
            contentCount={contents.length}
            treatmentCount={(hospital.treatments || []).length}
          />

          <FeaturedContent
            contents={contents}
            hospitalSlug={params.slug}
            hospitalName={hospital.name}
            directorName={hospital.director_name}
          />

          <DoctorIntro
            directorName={hospital.director_name}
            directorCareer={hospital.director_career}
            directorPhotoUrl={hospital.director_photo_url}
            specialties={hospital.specialties}
            region={hospital.region}
            contentCount={contents.length}
          />

          <TreatmentGrid treatments={hospital.treatments} />

          <ClinicGallery photos={hospital.photos ?? []} />

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
