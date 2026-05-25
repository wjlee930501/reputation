import { Metadata } from 'next'
import { notFound } from 'next/navigation'

import { fetchHospital, fetchContents, HospitalNotFoundError } from '@/lib/api'
import { getApiBase } from '@/lib/config'

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

export const revalidate = 3600

export async function generateStaticParams() {
  try {
    const apiBase = getApiBase(false)
    if (!apiBase) return []
    const res = await fetch(`${apiBase}/hospitals`, { next: { revalidate: 3600 } })
    if (!res.ok) return []
    const hospitals = (await res.json()) as Array<{ slug: string }>
    return hospitals.map((h) => ({ slug: h.slug }))
  } catch {
    return []
  }
}

function buildHospitalDescription(hospital: Awaited<ReturnType<typeof fetchHospital>>): string {
  const region = hospital.region?.join(' ') || ''
  const specialties = hospital.specialties?.join(', ') || ''
  const locality = [region, specialties].filter(Boolean).join(' · ')
  return locality
    ? `${hospital.name} (${locality}) 진료 정보 허브 — 환자가 자주 묻는 질문, 질환 정보, 치료 안내를 한곳에서 확인할 수 있습니다.`
    : `${hospital.name} 진료 정보 허브 — 환자가 자주 묻는 질문, 질환 정보, 치료 안내를 한곳에서 확인할 수 있습니다.`
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  try {
    const hospital = await fetchHospital(params.slug)
    const description = buildHospitalDescription(hospital)
    const ogImage = hospital.director_photo_url ?? undefined
    return {
      title: `${hospital.name} 진료 정보 허브`,
      description,
      alternates: { canonical: `/${params.slug}` },
      openGraph: {
        title: `${hospital.name} 진료 정보 허브`,
        description,
        url: `/${params.slug}`,
        type: 'website',
        images: ogImage
          ? [{ url: ogImage, alt: `${hospital.director_name || hospital.name} 원장` }]
          : undefined,
      },
      twitter: {
        card: 'summary_large_image',
        title: `${hospital.name} 진료 정보 허브`,
        description,
        images: ogImage ? [ogImage] : undefined,
      },
    }
  } catch {
    return { title: '진료 정보 허브' }
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
  } catch (e) {
    if (e instanceof HospitalNotFoundError) notFound()
    throw e
  }

  const sameAs = [
    hospital.website_url,
    hospital.blog_url,
    hospital.kakao_channel_url,
    hospital.google_business_profile_url,
    hospital.google_maps_url,
    hospital.naver_place_url,
    hospital.wikidata_qid ? `https://www.wikidata.org/wiki/${hospital.wikidata_qid}` : null,
    hospital.naver_place_id ? `https://map.naver.com/p/entry/place/${hospital.naver_place_id}` : null,
    hospital.kakao_place_id ? `https://place.map.kakao.com/${hospital.kakao_place_id}` : null,
  ].filter((value): value is string => Boolean(value))

  const clinicJsonLd = {
    '@context': 'https://schema.org',
    '@type': ['MedicalClinic', 'LocalBusiness'],
    '@id': `${SITE_URL}/${params.slug}#clinic`,
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
      '@id': `${SITE_URL}/${params.slug}/doctor#physician`,
      name: hospital.director_name,
      jobTitle: '원장',
      description: hospital.director_career,
      image: hospital.director_photo_url ?? undefined,
      url: `${SITE_URL}/${params.slug}/doctor`,
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
        <main id="main-content">
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

          <DoctorIntro
            directorName={hospital.director_name}
            directorCareer={hospital.director_career}
            directorPhotoUrl={hospital.director_photo_url}
            specialties={hospital.specialties}
            region={hospital.region}
            contentCount={contents.length}
          />

          <TreatmentGrid treatments={hospital.treatments} />

          <FeaturedContent
            contents={contents}
            hospitalSlug={params.slug}
            hospitalName={hospital.name}
            directorName={hospital.director_name}
          />

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
