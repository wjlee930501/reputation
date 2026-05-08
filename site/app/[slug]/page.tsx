import { Metadata } from 'next'
import { notFound } from 'next/navigation'

import { fetchHospital, fetchContents } from '@/lib/api'

import { Breadcrumb, buildBreadcrumbJsonLd } from './_components/Breadcrumb'
import { ClinicFooter } from './_components/ClinicFooter'
import { ClinicHeader } from './_components/ClinicHeader'
import { ClinicHero } from './_components/ClinicHero'
import { ContactCard } from './_components/ContactCard'
import { ContentTypeShowcase } from './_components/ContentTypeShowcase'
import { DoctorIntro } from './_components/DoctorIntro'
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
    const description = `${hospital.name}의 진료 정보, 원장 소개, 검수된 의료 콘텐츠. 환자 질문에 답하는 구조화된 데이터.`
    return {
      title: `${hospital.name} | 진료 정보 · 의료 콘텐츠`,
      description,
      alternates: { canonical: `/${params.slug}` },
      openGraph: {
        title: `${hospital.name} | 진료 정보 · 의료 콘텐츠`,
        description,
        url: `/${params.slug}`,
        type: 'website',
      },
    }
  } catch {
    return { title: '병원 정보' }
  }
}

export default async function HospitalPage({ params }: Props) {
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
    // 진료 항목은 검수되지 않은 자유 입력의 description을 노출하지 않는다.
    availableService: (hospital.treatments || []).map((treatment) => ({
      '@type': 'MedicalProcedure',
      name: treatment.name,
    })),
  }

  const breadcrumbJsonLd = buildBreadcrumbJsonLd(
    [
      { label: '홈', href: `/${params.slug}` },
    ],
    SITE_URL,
  )

  const externalLinks = [
    { url: hospital.website_url, label: '공식 홈페이지' },
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
        />
        <main>
          <ClinicHero
            hospitalName={hospital.name}
            hospitalSlug={params.slug}
            region={hospital.region}
            specialties={hospital.specialties}
            phone={hospital.phone}
          />

          <DoctorIntro
            directorName={hospital.director_name}
            directorCareer={hospital.director_career}
            directorPhotoUrl={hospital.director_photo_url}
            specialty={hospital.specialties[0] ?? null}
          />

          <TreatmentGrid treatments={hospital.treatments} />

          <ContentTypeShowcase
            contents={contents}
            hospitalSlug={params.slug}
            hospitalName={hospital.name}
          />

          <ContactCard
            address={hospital.address}
            phone={hospital.phone}
            businessHours={hospital.business_hours}
            googleMapsUrl={hospital.google_maps_url}
            links={externalLinks}
          />
        </main>
        <ClinicFooter
          hospitalName={hospital.name}
          address={hospital.address}
          phone={hospital.phone}
        />
      </div>
    </>
  )
}
